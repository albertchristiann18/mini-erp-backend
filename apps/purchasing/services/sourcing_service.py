import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from typing import Any

import openpyxl
import requests as http_requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from apps.inventory.models import Category, ProductVariant, Supplier
from apps.purchasing.models import SourcingPool, SourcingPoolItem
from core.models import Company

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"variant_name", "unit_price"}

CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class SourcingService:
    def get_or_create_pool(self, company: Company, supplier: Supplier) -> SourcingPool:
        pool, _ = SourcingPool.objects.get_or_create(company=company, supplier=supplier)
        return pool

    def build_template_workbook(self, company: Company) -> openpyxl.Workbook:
        wb = openpyxl.Workbook()

        ws_items = wb.active
        ws_items.title = "Items"
        ws_items.append(
            [
                "variant_code",
                "product_name",
                "variant_name",
                "category_code",
                "unit_price",
                "discounted_price",
                "qty_suggested",
                "supplier_link",
                "image_url",
                "notes",
            ]
        )

        ws_cats = wb.create_sheet("Categories")
        ws_cats.append(["category_code", "category_name"])
        for cat in Category.objects.filter(company=company, is_active=True).order_by("name"):
            ws_cats.append([cat.category_code, cat.name])

        return wb

    def parse_excel_preview(self, file_bytes: bytes, company: Company) -> dict[str, list[dict]]:
        try:
            wb = openpyxl.load_workbook(
                filename=io.BytesIO(file_bytes), read_only=True, data_only=True
            )
        except Exception:
            return {
                "valid": [],
                "errors": [{"row": 0, "message": "File is not a valid Excel workbook (.xlsx)."}],
            }

        if "Items" not in wb.sheetnames:
            return {
                "valid": [],
                "errors": [{"row": 0, "message": "Sheet named 'Items' not found."}],
            }

        ws = wb["Items"]
        rows = list(ws.iter_rows(values_only=True))
        MAX_ROWS = 5000
        if len(rows) > MAX_ROWS + 1:  # +1 for header row
            return {
                "valid": [],
                "errors": [
                    {
                        "row": 0,
                        "message": f"File exceeds the {MAX_ROWS}-row limit. Split into smaller files.",
                    }
                ],
            }
        if not rows:
            return {"valid": [], "errors": [{"row": 0, "message": "Items sheet is empty."}]}

        header_row = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        missing_required = REQUIRED_COLUMNS - set(header_row)
        if missing_required:
            return {
                "valid": [],
                "errors": [
                    {
                        "row": 1,
                        "message": f"Missing required columns: {', '.join(sorted(missing_required))}",
                    }
                ],
            }

        col_index: dict[str, int] = {col: header_row.index(col) for col in header_row if col}

        category_map: dict[str, dict[str, str]] = {
            cat.category_code: {"id": str(cat.id), "name": cat.name}
            for cat in Category.objects.filter(company=company, is_active=True)
        }

        # Single-query pre-pass to resolve all variant codes (avoids N+1).
        # get_cell is not yet defined here, so use raw index access.
        _vc_idx = col_index.get("variant_code")
        all_variant_codes: set[str] = set()
        for _row in rows[1:]:
            if _vc_idx is not None and _vc_idx < len(_row) and _row[_vc_idx]:
                _vc_raw = str(_row[_vc_idx]).strip()
                if _vc_raw:
                    all_variant_codes.add(_vc_raw)

        variant_code_map: dict[str, str] = {}  # sku_variant_code → variant_id str
        if all_variant_codes:
            # ProductVariant extends DefaultModel so has a direct company FK — no JOIN needed
            for pv in ProductVariant.objects.filter(
                sku_variant_code__in=all_variant_codes,
                company=company,
            ).only("id", "sku_variant_code"):
                variant_code_map[pv.sku_variant_code] = str(pv.id)

        valid: list[dict] = []
        errors: list[dict] = []

        def get_cell(row: tuple, col: str) -> str | None:
            idx = col_index.get(col)
            if idx is None:
                return None
            val = row[idx]
            return str(val).strip() if val is not None else None

        for row_num, row in enumerate(rows[1:], start=2):
            product_name = get_cell(row, "product_name")
            variant_name = get_cell(row, "variant_name")
            category_code = get_cell(row, "category_code")
            unit_price_raw = get_cell(row, "unit_price")
            variant_code = get_cell(row, "variant_code")

            # Skip blank rows — include variant_code in the check
            if not any([
                product_name,
                variant_name,
                category_code,
                unit_price_raw,
                variant_code,
                get_cell(row, "supplier_link"),
                get_cell(row, "image_url"),
            ]):
                continue

            row_errors: list[str] = []

            supplier_link_raw = get_cell(row, "supplier_link")
            if not product_name and not supplier_link_raw:
                row_errors.append("product_name or supplier_link is required")
            if not variant_name:
                row_errors.append("variant_name is required")

            # category_code is required only when no variant_code is provided
            if not variant_code and not category_code:
                row_errors.append("category_code is required")
            elif category_code and category_code not in category_map:
                row_errors.append(
                    f"category_code '{category_code}' not found — check the Categories sheet"
                )

            unit_price: Decimal | None = None
            if not unit_price_raw:
                row_errors.append("unit_price is required")
            else:
                try:
                    unit_price = Decimal(unit_price_raw)
                    if unit_price <= 0:
                        row_errors.append("unit_price must be greater than zero")
                except InvalidOperation:
                    row_errors.append(f"unit_price '{unit_price_raw}' is not a valid number")

            if row_errors:
                errors.append({"row": row_num, "message": "; ".join(row_errors)})
                continue

            discounted_price_raw = get_cell(row, "discounted_price")
            discounted_price: Decimal | None = None
            if discounted_price_raw:
                try:
                    discounted_price = Decimal(discounted_price_raw)
                    if discounted_price <= 0:
                        errors.append(
                            {
                                "row": row_num,
                                "message": "discounted_price must be greater than zero",
                            }
                        )
                        continue
                except InvalidOperation:
                    errors.append(
                        {
                            "row": row_num,
                            "message": f"discounted_price '{discounted_price_raw}' is not a valid number",
                        }
                    )
                    continue

            assert unit_price is not None
            if discounted_price is not None and discounted_price >= unit_price:
                errors.append(
                    {
                        "row": row_num,
                        "message": "discounted_price must be less than unit_price",
                    }
                )
                continue

            qty_suggested_raw = get_cell(row, "qty_suggested")
            qty_suggested: int | None = None
            if qty_suggested_raw:
                try:
                    qty_suggested = int(float(qty_suggested_raw))
                    if qty_suggested < 0:
                        errors.append(
                            {"row": row_num, "message": "qty_suggested must be 0 or greater"}
                        )
                        continue
                except ValueError:
                    errors.append(
                        {
                            "row": row_num,
                            "message": f"qty_suggested '{qty_suggested_raw}' must be a whole number",
                        }
                    )
                    continue

            # Resolve variant_code using the pre-built map (no per-row DB query)
            variant_id: str | None = None
            if variant_code:
                variant_id = variant_code_map.get(variant_code)
                if variant_id is None:
                    errors.append(
                        {
                            "row": row_num,
                            "message": (
                                f"variant_code '{variant_code}' not found — "
                                "no product variant with this SKU exists"
                            ),
                        }
                    )
                    continue

            # Category info (may be None for mapped rows where category_code was blank)
            cat_info = category_map.get(category_code) if category_code else None

            valid.append(
                {
                    "row": row_num,
                    "product_name": product_name,
                    "product_name_derived": not bool(product_name),
                    "variant_name": variant_name,
                    "variant_code": variant_code,
                    "variant_id": variant_id,
                    "category_code": category_code,
                    "category_id": cat_info["id"] if cat_info else None,
                    "category_name": cat_info["name"] if cat_info else None,
                    "unit_price": str(unit_price),
                    "discounted_price": str(discounted_price)
                    if discounted_price is not None
                    else None,
                    "qty_suggested": qty_suggested,
                    "supplier_link": get_cell(row, "supplier_link"),
                    "image_url": get_cell(row, "image_url"),
                    "notes": get_cell(row, "notes"),
                }
            )

        return {"valid": valid, "errors": errors}

    @transaction.atomic
    def import_rows(self, company: Company, supplier: Supplier, rows: list[dict]) -> dict[str, Any]:
        pool = self.get_or_create_pool(company=company, supplier=supplier)

        category_ids = {str(row["category_id"]) for row in rows if row.get("category_id")}
        valid_category_ids = set(
            Category.objects.filter(id__in=category_ids, company=company).values_list(
                "id", flat=True
            )
        )
        invalid_ids = category_ids - {str(cid) for cid in valid_category_ids}
        if invalid_ids:
            raise ValueError(f"category_id(s) not found for this company: {', '.join(invalid_ids)}")

        # Lock all pool items upfront to prevent duplicate-key races on concurrent imports
        def _item_merge_key(item: SourcingPoolItem) -> tuple[str, str]:
            key_part = (item.supplier_link or item.product_name or "").lower()
            return (key_part, item.variant_name.lower())

        existing_items = list(SourcingPoolItem.objects.filter(pool=pool).select_for_update())
        existing_map: dict[tuple[str, str], SourcingPoolItem] = {
            _item_merge_key(item): item for item in existing_items
        }
        # Secondary index: look up by (product_name, variant_name) so that re-importing
        # the same item with supplier_link added doesn't create a duplicate.
        existing_product_name_map: dict[tuple[str, str], SourcingPoolItem] = {
            (item.product_name.lower(), item.variant_name.lower()): item
            for item in existing_items
            if item.product_name
        }

        in_progress_creates: dict[tuple[str, str], SourcingPoolItem] = {}

        to_create: list[SourcingPoolItem] = []
        to_update: list[SourcingPoolItem] = []

        variant_ids = {str(row["variant_id"]) for row in rows if row.get("variant_id")}
        if variant_ids:
            valid_variant_ids = set(
                ProductVariant.objects.filter(id__in=variant_ids, company=company).values_list(
                    "id", flat=True
                )
            )
            invalid_variant_ids = variant_ids - {str(vid) for vid in valid_variant_ids}
            if invalid_variant_ids:
                raise ValueError(f"variant_id(s) no longer exist: {', '.join(invalid_variant_ids)}")

        for row in rows:
            try:
                product_name_raw = row.get("product_name")
                product_name_val: str | None = str(product_name_raw).strip() if product_name_raw else None
                variant_name: str = str(row["variant_name"]).strip()
                category_id_val: str | None = row.get("category_id") or None
                unit_price = Decimal(str(row["unit_price"]))
                variant_code_val: str | None = row.get("variant_code") or None
                variant_id_val: str | None = row.get("variant_id") or None
            except (KeyError, InvalidOperation) as exc:
                raise ValueError(f"Invalid row data: {exc}") from exc

            if not variant_name:
                raise ValueError("variant_name must not be empty")

            supplier_link: str | None = row.get("supplier_link") or None
            if not product_name_val and not supplier_link:
                raise ValueError("product_name or supplier_link is required")
            if unit_price <= 0:
                raise ValueError(f"unit_price must be > 0, got {unit_price}")

            discounted_raw = row.get("discounted_price")
            discounted_price: Decimal | None = None
            if discounted_raw is not None:
                try:
                    discounted_price = Decimal(str(discounted_raw))
                    if discounted_price <= 0:
                        raise ValueError(f"discounted_price must be > 0, got {discounted_price}")
                except InvalidOperation as exc:
                    raise ValueError(f"Invalid discounted_price: {discounted_raw}") from exc

            if discounted_price is not None and discounted_price >= unit_price:
                raise ValueError(
                    f"discounted_price must be less than unit_price "
                    f"(got {discounted_price} >= {unit_price})"
                )

            qty_suggested: int | None = row.get("qty_suggested")
            if qty_suggested is not None:
                qty_suggested = int(float(str(qty_suggested)))
                if qty_suggested < 0:
                    raise ValueError(f"qty_suggested must be >= 0, got {qty_suggested}")

            image_url: str | None = row.get("image_url") or None
            notes: str | None = row.get("notes") or None

            key_part = (supplier_link or product_name_val or "").lower()
            key = (key_part, variant_name.lower())

            existing_in_db = existing_map.get(key)
            # Fallback: row now has supplier_link but was previously stored under product_name key.
            # Without this, adding a supplier_link to a row on re-import would create a duplicate.
            if existing_in_db is None and supplier_link and product_name_val:
                fallback_key = (product_name_val.lower(), variant_name.lower())
                existing_in_db = existing_product_name_map.get(fallback_key)
            existing_in_batch = in_progress_creates.get(key)
            existing = existing_in_db or existing_in_batch

            if existing:
                image_url_changed = existing.image_url != image_url
                existing.product_name = product_name_val
                existing.variant_name = variant_name
                # Preserve the existing category when re-importing a mapped row that carries no
                # category_code — only update category if a new one is provided or this is not a mapped row.
                if category_id_val is not None or variant_id_val is None:
                    existing.category_id = category_id_val  # type: ignore[attr-defined]
                existing.unit_price = unit_price
                existing.discounted_price = discounted_price
                existing.qty_suggested = qty_suggested
                existing.supplier_link = supplier_link
                existing.notes = notes
                existing.variant_code = variant_code_val
                existing.variant_id = variant_id_val  # type: ignore[attr-defined]
                if image_url_changed:
                    existing.image_url = image_url
                    existing.image_file = None
                    existing.image_download_status = SourcingPoolItem.ImageDownloadStatus.PENDING
                if existing_in_db and existing not in to_update:
                    to_update.append(existing)
            else:
                new_item = SourcingPoolItem(
                    pool=pool,
                    company=company,
                    product_name=product_name_val,
                    variant_name=variant_name,
                    category_id=category_id_val,
                    unit_price=unit_price,
                    discounted_price=discounted_price,
                    qty_suggested=qty_suggested,
                    supplier_link=supplier_link,
                    image_url=image_url,
                    image_download_status=SourcingPoolItem.ImageDownloadStatus.PENDING,
                    notes=notes,
                    variant_code=variant_code_val,
                    variant_id=variant_id_val,
                )
                in_progress_creates[key] = new_item
                to_create.append(new_item)

        if to_create:
            SourcingPoolItem.objects.bulk_create(to_create)
        if to_update:
            SourcingPoolItem.objects.bulk_update(
                to_update,
                fields=[
                    "product_name",
                    "variant_name",
                    "category_id",
                    "unit_price",
                    "discounted_price",
                    "qty_suggested",
                    "supplier_link",
                    "image_url",
                    "image_file",
                    "image_download_status",
                    "notes",
                    "variant_code",
                    "variant_id",
                ],
            )

        return {"created": len(to_create), "updated": len(to_update), "pool_id": str(pool.id)}

    def download_pool_images(
        self,
        pool: SourcingPool,
        include_failed: bool = False,
    ) -> dict[str, int]:
        status_filter = [SourcingPoolItem.ImageDownloadStatus.PENDING]
        if include_failed:
            status_filter.append(SourcingPoolItem.ImageDownloadStatus.FAILED)

        all_candidates = list(
            SourcingPoolItem.objects.filter(
                pool=pool,
                image_download_status__in=status_filter,
            )
        )
        items = [i for i in all_candidates if i.image_url]
        skipped = len(all_candidates) - len(items)

        if not items:
            return {"done": 0, "failed": 0, "skipped": skipped}

        done_items: list[SourcingPoolItem] = []
        failed_items: list[SourcingPoolItem] = []

        def _download_one(item: SourcingPoolItem) -> tuple[SourcingPoolItem, bool, str | None]:
            try:
                resp = http_requests.get(item.image_url, timeout=10)  # type: ignore[arg-type]
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                ext = CONTENT_TYPE_EXT.get(content_type, ".jpg")
                r2_path = f"sourcing/images/{item.id}{ext}"
                saved_path = default_storage.save(r2_path, ContentFile(resp.content))
                item.image_file.name = saved_path  # type: ignore[attr-defined]
                item.image_download_status = SourcingPoolItem.ImageDownloadStatus.DONE
                return (item, True, None)
            except Exception as exc:
                item.image_download_status = SourcingPoolItem.ImageDownloadStatus.FAILED
                return (item, False, str(exc))

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_download_one, item): item for item in items}
            for future in as_completed(futures):
                item, success, error_msg = future.result()
                if success:
                    done_items.append(item)
                else:
                    logger.warning(
                        "Image download failed for SourcingPoolItem %s (url=%s): %s",
                        item.id,
                        item.image_url,
                        error_msg,
                    )
                    failed_items.append(item)

        if done_items:
            SourcingPoolItem.objects.bulk_update(
                done_items, fields=["image_file", "image_download_status"]
            )
        if failed_items:
            SourcingPoolItem.objects.bulk_update(failed_items, fields=["image_download_status"])

        return {"done": len(done_items), "failed": len(failed_items), "skipped": skipped}
