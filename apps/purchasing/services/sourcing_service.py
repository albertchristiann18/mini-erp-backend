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
from django.utils import timezone

from apps.inventory.models import Category, ProductVariant, Supplier
from apps.purchasing.models import ColorAbbreviation, SourcingPool, SourcingPoolItem
from core.models import Company

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"unit_price"}

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
                "dim1_key",
                "dim1_value",
                "dim2_key",
                "dim2_value",
                "category_code",
                "unit_price",
                "discounted_price",
                "order_qty",
                "supplier_link",
                "image_url",
                "notes",
            ]
        )

        ws_cats = wb.create_sheet("Categories")
        ws_cats.append(["category_code", "category_name"])
        for cat in Category.objects.filter(company=company, is_active=True).order_by("name"):
            ws_cats.append([cat.category_code, cat.name])

        ws_guide = wb.create_sheet("Guide")

        ws_guide.column_dimensions["A"].width = 20
        ws_guide.column_dimensions["B"].width = 60

        guide_rows = [
            ["PANDUAN IMPORT SOURCING POOL", ""],
            ["", ""],
            ["KOLOM", "KETERANGAN"],
            [
                "variant_code",
                "Opsional. SKU varian yang sudah ada di sistem. Jika diisi, baris ini ditautkan ke produk yang sudah ada.",
            ],
            [
                "product_name",
                "Nama produk. Wajib jika supplier_link kosong. Semua baris dengan product_name yang sama dikelompokkan sebagai satu produk.",
            ],
            [
                "dim1_key",
                "Nama dimensi pertama, mis. 'Warna' (opsional — kosongkan untuk produk 1 varian).",
            ],
            [
                "dim1_value",
                "Nilai dimensi pertama, mis. 'Putih'.",
            ],
            [
                "dim2_key",
                "Nama dimensi kedua, mis. 'Ukuran' (opsional).",
            ],
            [
                "dim2_value",
                "Nilai dimensi kedua, mis. 'S'.",
            ],
            [
                "category_code",
                "Kode kategori dari sheet Categories. Boleh kosong — kategori baru akan dibuat otomatis saat import.",
            ],
            ["unit_price", "Harga beli per unit (angka saja, mis. 50000)."],
            ["discounted_price", "Harga diskon. Harus lebih kecil dari unit_price. Boleh kosong."],
            ["order_qty", "Jumlah yang disarankan untuk dipesan. Boleh kosong."],
            ["supplier_link", "URL halaman produk supplier. Wajib jika product_name kosong."],
            ["image_url", "URL gambar produk. Gambar akan diunduh otomatis. Boleh kosong."],
            ["notes", "Catatan tambahan. Boleh kosong."],
            ["", ""],
            ["CATATAN: variant_name TIDAK LAGI DIIMPUT MANUAL", ""],
            ["variant_name sekarang dihasilkan otomatis dari nilai dimensi:", ""],
            [
                "  - Jika dim1 + dim2 diisi: variant_name = '{dim1_value}-{dim2_value}' (mis. Putih-S)",
                "",
            ],
            ["  - Jika hanya dim1 diisi: variant_name = dim1_value (mis. Putih)", ""],
            ["  - Jika hanya dim2 diisi: variant_name = dim2_value", ""],
            ["", ""],
            ["CONTOH: PRODUK DENGAN 2 DIMENSI (Warna + Ukuran)", ""],
            ["", ""],
            ["Untuk produk 'Kaos Polo' dengan Warna (Putih, Merah) dan Ukuran (S, M, L):", ""],
            ["Buat 6 baris dengan product_name yang sama dan kombinasi dim yang berbeda:", ""],
            ["", ""],
            [
                "product_name | dim1_key | dim1_value | dim2_key | dim2_value | unit_price | order_qty"
            ],
            ["Kaos Polo   | Warna    | Putih      | Ukuran   | S          | 50000      | 5"],
            ["Kaos Polo   | Warna    | Putih      | Ukuran   | M          | 50000      | 10"],
            ["Kaos Polo   | Warna    | Putih      | Ukuran   | L          | 50000      | 8"],
            ["Kaos Polo   | Warna    | Merah      | Ukuran   | S          | 50000      | 5"],
            ["Kaos Polo   | Warna    | Merah      | Ukuran   | M          | 50000      | 10"],
            ["Kaos Polo   | Warna    | Merah      | Ukuran   | L          | 50000      | 8"],
            ["", ""],
            ["CONTOH: PRODUK DENGAN 1 DIMENSI (hanya Ukuran)", ""],
            ["", ""],
            [
                "product_name | dim1_key | dim1_value | dim2_key | dim2_value | unit_price | order_qty"
            ],
            ["Celana Cargo | Ukuran   | S          |          |            | 80000      | 5"],
            ["Celana Cargo | Ukuran   | M          |          |            | 80000      | 10"],
            ["Celana Cargo | Ukuran   | L          |          |            | 80000      | 8"],
            ["", ""],
            ["CONTOH: TANPA DIMENSI (produk 1 varian)", ""],
            ["", ""],
            ["Cukup isi unit_price + supplier_link, biarkan kolom dim kosong:", ""],
            ["product_name | unit_price | supplier_link"],
            ["Produk Tunggal | 50000 | https://...", ""],
        ]
        for row_data in guide_rows:
            ws_guide.append(row_data)

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
        if len(rows) > MAX_ROWS + 1:
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

        _vc_idx = col_index.get("variant_code")
        all_variant_codes: set[str] = set()
        for _row in rows[1:]:
            if _vc_idx is not None and _vc_idx < len(_row) and _row[_vc_idx]:
                _vc_raw = str(_row[_vc_idx]).strip()
                if _vc_raw:
                    all_variant_codes.add(_vc_raw)

        variant_code_map: dict[str, str] = {}
        if all_variant_codes:
            for pv in ProductVariant.objects.filter(
                sku_variant_code__in=all_variant_codes,
                company=company,
            ).only("id", "sku_variant_code"):
                variant_code_map[pv.sku_variant_code] = str(pv.id)

        # Pre-load ColorAbbreviation for this company
        color_abbreviations = set(
            ColorAbbreviation.objects.filter(company=company).values_list("color_name", flat=True)
        )

        valid: list[dict] = []
        errors: list[dict] = []
        missing_colors_list: list[dict] = []
        missing_product_names_list: list[dict] = []
        dim_mismatches_list: list[dict] = []

        def get_cell(row: tuple, col: str) -> str | None:
            idx = col_index.get(col)
            if idx is None or idx >= len(row):
                return None
            val = row[idx]
            return str(val).strip() if val is not None else None

        # First pass: collect per-row data for cross-row checks
        row_data_list: list[dict] = []

        for row_num, row in enumerate(rows[1:], start=2):
            product_name = get_cell(row, "product_name") or ""
            variant_code = get_cell(row, "variant_code") or ""
            category_code = get_cell(row, "category_code") or ""
            unit_price_raw = get_cell(row, "unit_price")
            supplier_link_raw = get_cell(row, "supplier_link") or ""
            dim1_key_val = get_cell(row, "dim1_key") or ""
            dim1_value_val = get_cell(row, "dim1_value") or ""
            dim2_key_val = get_cell(row, "dim2_key") or ""
            dim2_value_val = get_cell(row, "dim2_value") or ""

            # Blank-row detection
            if not any(
                [
                    product_name,
                    dim1_value_val,
                    dim2_value_val,
                    unit_price_raw,
                    variant_code,
                    supplier_link_raw,
                    get_cell(row, "image_url"),
                ]
            ):
                continue

            row_errors: list[str] = []

            # Error 1: dim1_key filled but dim1_value empty
            if dim1_key_val and not dim1_value_val:
                row_errors.append("dim1_key is filled but dim1_value is empty")

            # Error 2: dim2_key filled but dim2_value empty
            if dim2_key_val and not dim2_value_val:
                row_errors.append("dim2_key is filled but dim2_value is empty")

            # Error 3: product_name AND supplier_link AND variant_code all blank
            if not product_name and not supplier_link_raw and not variant_code:
                row_errors.append("product_name or supplier_link is required")

            # Derive variant_name for error checking
            if dim1_value_val and dim2_value_val:
                derived_variant_name = f"{dim1_value_val}-{dim2_value_val}"
            elif dim1_value_val:
                derived_variant_name = dim1_value_val
            elif dim2_value_val:
                derived_variant_name = dim2_value_val
            else:
                derived_variant_name = ""

            unit_price: Decimal | None = None
            # Error 4: unit_price required
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

            discounted_price: Decimal | None = None
            discounted_price_raw_val = get_cell(row, "discounted_price")
            if discounted_price_raw_val:
                try:
                    discounted_price = Decimal(discounted_price_raw_val)
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
                            "message": f"discounted_price '{discounted_price_raw_val}' is not a valid number",
                        }
                    )
                    continue

            assert unit_price is not None

            # discounted_price == unit_price → silently set to None
            if discounted_price is not None and discounted_price >= unit_price:
                if discounted_price == unit_price:
                    discounted_price = None
                else:
                    errors.append(
                        {
                            "row": row_num,
                            "message": "discounted_price must be less than unit_price",
                        }
                    )
                    continue

            order_qty_raw_val = get_cell(row, "order_qty") or get_cell(row, "qty_suggested")
            qty_suggested: int | None = None
            if order_qty_raw_val:
                try:
                    qty_suggested = int(float(order_qty_raw_val))
                    if qty_suggested < 0:
                        errors.append({"row": row_num, "message": "order_qty must be 0 or greater"})
                        continue
                except ValueError:
                    errors.append(
                        {
                            "row": row_num,
                            "message": f"order_qty '{order_qty_raw_val}' must be a whole number",
                        }
                    )
                    continue

            # variant_code not found in DB → no longer an error, pass through
            variant_id: str | None = None
            if variant_code:
                variant_id = variant_code_map.get(variant_code)

            cat_info = category_map.get(category_code) if category_code else None

            row_data = {
                "row": row_num,
                "product_name": product_name or None,
                "variant_name": derived_variant_name,
                "variant_code": variant_code or None,
                "variant_id": variant_id,
                "category_code": category_code or None,
                "category_id": cat_info["id"] if cat_info else None,
                "category_name": cat_info["name"] if cat_info else None,
                "unit_price": str(unit_price),
                "discounted_price": str(discounted_price) if discounted_price is not None else None,
                "qty_suggested": qty_suggested,
                "supplier_link": supplier_link_raw or None,
                "image_url": get_cell(row, "image_url"),
                "notes": get_cell(row, "notes"),
                "dim1_key": dim1_key_val or None,
                "dim1_value": dim1_value_val or None,
                "dim2_key": dim2_key_val or None,
                "dim2_value": dim2_value_val or None,
            }

            valid.append(row_data)

            # Detect missing_colors
            color_like_keys = {"color", "warna", "colour"}
            if dim1_key_val and dim1_key_val.lower() in color_like_keys and dim1_value_val:
                if dim1_value_val not in color_abbreviations:
                    missing_colors_list.append({"color_name": dim1_value_val})
            if dim2_key_val and dim2_key_val.lower() in color_like_keys and dim2_value_val:
                if dim2_value_val not in color_abbreviations:
                    missing_colors_list.append({"color_name": dim2_value_val})

            # Detect missing_product_names
            if not product_name and not supplier_link_raw and not variant_code:
                missing_product_names_list.append(
                    {
                        "row": row_num,
                        "supplier_link": None,
                        "dim1_key": dim1_key_val or None,
                        "dim1_value": dim1_value_val or None,
                        "dim2_key": dim2_key_val or None,
                        "dim2_value": dim2_value_val or None,
                        "unit_price": str(unit_price),
                    }
                )

            # Detect dim_mismatches
            if variant_code and dim1_value_val:
                dim_mismatches_list.append(
                    {
                        "row": row_num,
                        "variant_code": variant_code,
                        "dim1_key": dim1_key_val or None,
                        "dim1_value": dim1_value_val or None,
                        "dim2_key": dim2_key_val or None,
                        "dim2_value": dim2_value_val or None,
                    }
                )

            row_data_list.append(row_data)

        # Cross-row checks (pass 2)
        product_groups: dict[str, list[dict]] = {}
        for rd in row_data_list:
            pn = rd["product_name"]
            if pn:
                pn_lower = pn.lower()
                if pn_lower not in product_groups:
                    product_groups[pn_lower] = []
                product_groups[pn_lower].append(rd)

        for pn_lower, group_rows in product_groups.items():
            if len(group_rows) < 2:
                continue
            # Check dim1_key consistency
            dim1_keys = {r["dim1_key"] for r in group_rows if r["dim1_key"]}
            if len(dim1_keys) > 1:
                errors.append(
                    {
                        "row": group_rows[1]["row"],
                        "message": f"Inconsistent dim1_key for product '{group_rows[0]['product_name']}': {', '.join(sorted(dim1_keys))}",
                    }
                )
            # Check dim2_key consistency
            dim2_keys = {r["dim2_key"] for r in group_rows if r["dim2_key"]}
            if len(dim2_keys) > 1:
                errors.append(
                    {
                        "row": group_rows[1]["row"],
                        "message": f"Inconsistent dim2_key for product '{group_rows[0]['product_name']}': {', '.join(sorted(dim2_keys))}",
                    }
                )
            # Check category_code consistency
            cat_codes = {r["category_code"] for r in group_rows if r["category_code"]}
            if len(cat_codes) > 1:
                errors.append(
                    {
                        "row": group_rows[1]["row"],
                        "message": f"Inconsistent category_code for product '{group_rows[0]['product_name']}': {', '.join(sorted(cat_codes))}",
                    }
                )

        # Deduplicate missing_colors
        seen_colors: set[str] = set()
        unique_missing_colors: list[dict] = []
        for mc in missing_colors_list:
            cn = mc["color_name"]
            if cn not in seen_colors:
                seen_colors.add(cn)
                unique_missing_colors.append(mc)

        return {
            "valid": valid,
            "errors": errors,
            "missing_colors": unique_missing_colors,
            "missing_product_names": missing_product_names_list,
            "dim_mismatches": dim_mismatches_list,
        }

    @transaction.atomic
    def import_rows(self, company: Company, supplier: Supplier, rows: list[dict]) -> dict[str, Any]:
        pool = self.get_or_create_pool(company=company, supplier=supplier)

        # Auto-create categories for rows where category_code is new (category_id=None at preview time).
        for row in rows:
            if not row.get("category_id") and row.get("category_code"):
                cat, _ = Category.objects.get_or_create(
                    company=company,
                    category_code=row["category_code"],
                    defaults={"name": row["category_code"]},
                )
                row["category_id"] = str(cat.id)

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
                product_name_val: str | None = (
                    str(product_name_raw).strip() if product_name_raw else None
                )
                category_id_val: str | None = row.get("category_id") or None
                unit_price = Decimal(str(row["unit_price"]))
                variant_code_val: str | None = row.get("variant_code") or None
                variant_id_val: str | None = row.get("variant_id") or None
            except (KeyError, InvalidOperation) as exc:
                raise ValueError(f"Invalid row data: {exc}") from exc

            # Read dim values
            dim1_key_val = str(row.get("dim1_key") or "").strip()
            dim1_value_val = str(row.get("dim1_value") or "").strip()
            dim2_key_val = str(row.get("dim2_key") or "").strip()
            dim2_value_val = str(row.get("dim2_value") or "").strip()

            # Derive variant_name from dims
            if dim1_value_val and dim2_value_val:
                variant_name = f"{dim1_value_val}-{dim2_value_val}"
            elif dim1_value_val:
                variant_name = dim1_value_val
            elif dim2_value_val:
                variant_name = dim2_value_val
            else:
                variant_name = str(row.get("variant_name") or "").strip()

            if not variant_name:
                raise ValueError("variant_name must not be empty")

            supplier_link: str | None = row.get("supplier_link") or None
            if not product_name_val and not supplier_link and not variant_code_val:
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

            # discounted_price == unit_price → treat as no discount
            if discounted_price is not None and discounted_price >= unit_price:
                if discounted_price == unit_price:
                    discounted_price = None
                else:
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

            key_part = (supplier_link or product_name_val or variant_code_val or "").lower()
            key = (key_part, variant_name.lower())

            # Carry-over variant_id from existing pool item if no variant_code provided
            if not variant_code_val and not variant_id_val:
                _carry_key = (
                    (supplier_link or product_name_val or "").lower(),
                    variant_name.lower(),
                )
                _existing_carry = existing_map.get(_carry_key)
                if _existing_carry:
                    _existing_variant_id = getattr(_existing_carry, "variant_id", None)
                    if _existing_variant_id:
                        variant_id_val = str(_existing_variant_id)

            existing_in_db = existing_map.get(key)
            if existing_in_db is None and supplier_link and product_name_val:
                fallback_key = (product_name_val.lower(), variant_name.lower())
                existing_in_db = existing_product_name_map.get(fallback_key)
            existing_in_batch = in_progress_creates.get(key)
            existing = existing_in_db or existing_in_batch

            if existing:
                image_url_changed = existing.image_url != image_url
                existing.product_name = product_name_val
                existing.variant_name = variant_name
                if category_id_val is not None or variant_id_val is None:
                    existing.category_id = category_id_val  # type: ignore[attr-defined]
                existing.unit_price = unit_price
                existing.discounted_price = discounted_price
                existing.qty_suggested = qty_suggested
                existing.supplier_link = supplier_link
                existing.notes = notes
                existing.variant_code = variant_code_val
                setattr(existing, "variant_id", variant_id_val)
                existing.dim1_key = dim1_key_val
                existing.dim1_value = dim1_value_val
                existing.dim2_key = dim2_key_val
                existing.dim2_value = dim2_value_val
                existing.last_active_at = timezone.now()
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
                    dim1_key=dim1_key_val,
                    dim1_value=dim1_value_val,
                    dim2_key=dim2_key_val,
                    dim2_value=dim2_value_val,
                    last_active_at=timezone.now(),
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
                    "dim1_key",
                    "dim1_value",
                    "dim2_key",
                    "dim2_value",
                    "last_active_at",
                ],
            )

        return {
            "created": len(to_create),
            "updated": len(to_update),
            "pool_id": str(pool.id),
            "item_ids": [str(item.id) for item in to_create + to_update],
        }

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
