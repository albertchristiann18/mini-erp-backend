import io
from decimal import Decimal, InvalidOperation
from typing import Any

import openpyxl
from django.db import transaction

from apps.inventory.models import Category, Supplier
from apps.purchasing.models import SourcingPool, SourcingPoolItem
from core.models import Company

REQUIRED_COLUMNS = {"product_name", "variant_name", "category_code", "unit_price"}


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
                "errors": [{"row": 0, "message": f"File exceeds the {MAX_ROWS}-row limit. Split into smaller files."}],
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

            if not any([product_name, variant_name, category_code, unit_price_raw]):
                continue

            row_errors: list[str] = []

            if not product_name:
                row_errors.append("product_name is required")
            if not variant_name:
                row_errors.append("variant_name is required")
            if not category_code:
                row_errors.append("category_code is required")
            elif category_code not in category_map:
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

            cat_info = category_map[category_code]  # type: ignore[index]
            valid.append(
                {
                    "row": row_num,
                    "product_name": product_name,
                    "variant_name": variant_name,
                    "category_code": category_code,
                    "category_id": cat_info["id"],
                    "category_name": cat_info["name"],
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

        existing_map: dict[tuple[str, str], SourcingPoolItem] = {
            (item.product_name.lower(), item.variant_name.lower()): item
            for item in SourcingPoolItem.objects.filter(pool=pool).select_for_update()
        }

        in_progress_creates: dict[tuple[str, str], SourcingPoolItem] = {}

        to_create: list[SourcingPoolItem] = []
        to_update: list[SourcingPoolItem] = []

        for row in rows:
            try:
                product_name: str = str(row["product_name"]).strip()
                variant_name: str = str(row["variant_name"]).strip()
                category_id: str = str(row["category_id"])
                unit_price = Decimal(str(row["unit_price"]))
            except (KeyError, InvalidOperation) as exc:
                raise ValueError(f"Invalid row data: {exc}") from exc

            if not product_name or not variant_name:
                raise ValueError("product_name and variant_name must not be empty")
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

            supplier_link: str | None = row.get("supplier_link") or None
            image_url: str | None = row.get("image_url") or None
            notes: str | None = row.get("notes") or None

            key = (product_name.lower(), variant_name.lower())

            existing_in_db = existing_map.get(key)
            existing_in_batch = in_progress_creates.get(key)
            existing = existing_in_db or existing_in_batch

            if existing:
                image_url_changed = existing.image_url != image_url
                existing.product_name = product_name
                existing.variant_name = variant_name
                existing.category_id = category_id  # type: ignore[attr-defined]
                existing.unit_price = unit_price
                existing.discounted_price = discounted_price
                existing.qty_suggested = qty_suggested
                existing.supplier_link = supplier_link
                existing.notes = notes
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
                    product_name=product_name,
                    variant_name=variant_name,
                    category_id=category_id,
                    unit_price=unit_price,
                    discounted_price=discounted_price,
                    qty_suggested=qty_suggested,
                    supplier_link=supplier_link,
                    image_url=image_url,
                    image_download_status=SourcingPoolItem.ImageDownloadStatus.PENDING,
                    notes=notes,
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
                ],
            )

        return {"created": len(to_create), "updated": len(to_update), "pool_id": str(pool.id)}
