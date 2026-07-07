import logging
from dataclasses import dataclass
from decimal import Decimal

import ulid
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404

from apps.inventory.models import Category, Product, ProductVariant
from apps.purchasing.models import ColorAbbreviation, PurchaseOrder, PurchaseOrderDetail
from apps.purchasing.services.sourcing_service import generate_variant_suffix

logger = logging.getLogger(__name__)


@dataclass
class SourcingRow:
    row_key: str
    product_name: str | None
    variant_name: str
    variant_code: str | None
    variant_id: str | None
    category_id: str | None
    unit_price: Decimal
    discounted_price: Decimal | None
    qty_suggested: int | None
    supplier_link: str | None
    dim1_key: str
    dim1_value: str
    dim2_key: str
    dim2_value: str
    dim_mismatch_resolution: str | None


def _create_po_detail(
    po: PurchaseOrder,
    variant_id: str,
    sourcing_row: SourcingRow,
    ordered_qty: int,
) -> PurchaseOrderDetail:
    exchange_rate = Decimal(str(po.exchange_rate or 0))
    unit_price_foreign = sourcing_row.unit_price
    discounted_price_foreign = sourcing_row.discounted_price or unit_price_foreign

    unit_price_base = int(round(unit_price_foreign * exchange_rate))
    discounted_unit_price_base = int(round(discounted_price_foreign * exchange_rate))

    return PurchaseOrderDetail.objects.create(
        purchase_order=po,
        company=po.company,
        product_variant_id=variant_id,
        ordered_qty=ordered_qty,
        unit_price_foreign=unit_price_foreign,
        unit_price_base=unit_price_base,
        total_price_foreign=unit_price_foreign * ordered_qty,
        total_price_base=unit_price_base * ordered_qty,
        discounted_unit_price_foreign=discounted_price_foreign,
        discounted_unit_price_base=discounted_unit_price_base,
        discounted_total_price_foreign=discounted_price_foreign * ordered_qty,
        discounted_total_price_base=discounted_unit_price_base * ordered_qty,
        supplier_link=sourcing_row.supplier_link,
    )


def _build_variant_name(sourcing_row: SourcingRow) -> str:
    parts = [v for v in [sourcing_row.dim1_value, sourcing_row.dim2_value] if v]
    return " / ".join(parts) or "Default"


def _derive_row_key(row: dict, i: int, variant_name: str) -> str:
    if row.get("supplier_link"):
        return f"{row['supplier_link']}||{variant_name}"
    elif row.get("product_name"):
        return f"{row['product_name']}||{variant_name}"
    else:
        return str(i)


def _derive_variant_name(row: dict) -> str:
    dim1_value = str(row.get("dim1_value") or "").strip()
    dim2_value = str(row.get("dim2_value") or "").strip()
    if dim1_value and dim2_value:
        return f"{dim1_value}-{dim2_value}"
    elif dim1_value:
        return dim1_value
    elif dim2_value:
        return dim2_value
    return str(row.get("variant_name") or "Default").strip()


def _is_valid_ulid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        ulid.parse(value)
        return True
    except (ValueError, TypeError):
        return False


def _validate_referenced_ids(
    ids_raw: set[str], model: type[Category] | type[ProductVariant], company_id: str, label: str
) -> None:
    valid_format_ids = {i for i in ids_raw if _is_valid_ulid(i)}
    malformed_ids = ids_raw - valid_format_ids
    if valid_format_ids:
        existing_ids = set(
            model.objects.filter(id__in=valid_format_ids, company_id=company_id).values_list(
                "id", flat=True
            )
        )
        invalid_ids = valid_format_ids - {str(eid) for eid in existing_ids}
    else:
        invalid_ids = set()
    invalid_ids |= malformed_ids
    if invalid_ids:
        raise ValidationError(f"Invalid {label}(s): {', '.join(invalid_ids)}")


def _record_added(added: list[dict], sr: SourcingRow, po_detail: PurchaseOrderDetail) -> None:
    added.append(
        {
            "item_id": sr.row_key,
            "po_detail_id": str(po_detail.id),
            "product_name": sr.product_name or "",
            "variant_name": sr.variant_name,
        }
    )


def _record_skipped(skipped: list[dict], sr: SourcingRow, reason: str) -> None:
    skipped.append(
        {
            "item_id": sr.row_key,
            "product_name": sr.product_name or "",
            "variant_name": sr.variant_name,
            "reason": reason,
        }
    )


def _build_sourcing_row(
    row: dict, i: int, dim_mismatch_resolutions: dict[str, str] | None
) -> SourcingRow:
    dim1_value = str(row.get("dim1_value") or "").strip()
    dim2_value = str(row.get("dim2_value") or "").strip()
    variant_name = _derive_variant_name(row)

    row_key = _derive_row_key(row, i, variant_name)

    unit_price = Decimal(str(row["unit_price"]))

    discounted_raw = row.get("discounted_price")
    discounted_price: Decimal | None = None
    if discounted_raw is not None and str(discounted_raw).strip():
        discounted_price = Decimal(str(discounted_raw))

    qty_raw = row.get("qty_suggested")
    qty_suggested: int | None = None
    if qty_raw is not None:
        qty_suggested = int(qty_raw)

    return SourcingRow(
        row_key=row_key,
        product_name=str(row.get("product_name") or "").strip() or None,
        variant_name=variant_name,
        variant_code=str(row.get("variant_code") or "").strip() or None,
        variant_id=str(row.get("variant_id") or "").strip() or None,
        category_id=str(row.get("category_id") or "").strip() or None,
        unit_price=unit_price,
        discounted_price=discounted_price,
        qty_suggested=qty_suggested,
        supplier_link=str(row.get("supplier_link") or "").strip() or None,
        dim1_key=row.get("dim1_key") or "",
        dim1_value=dim1_value,
        dim2_key=row.get("dim2_key") or "",
        dim2_value=dim2_value,
        dim_mismatch_resolution=(dim_mismatch_resolutions or {}).get(row_key),
    )


class SourcingProductService:
    @transaction.atomic
    def import_and_add(
        self,
        po: PurchaseOrder,
        supplier_id: str,
        rows: list[dict],
        dim_mismatch_resolutions: dict[str, str] | None = None,
    ) -> dict:
        if po.status not in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
            raise ValidationError(f"Cannot add items to a PO with status {po.status}.")

        from apps.inventory.models import Supplier

        if not Supplier.objects.filter(id=supplier_id, company=po.company).exists():
            raise Http404("Supplier not found.")

        dim_mismatch_resolutions = dim_mismatch_resolutions or {}

        row_keys_seen: set[str] = set()
        deduped_rows: list[dict] = []

        for i, row in enumerate(rows):
            unit_price_raw = row.get("unit_price")
            if unit_price_raw is None or Decimal(str(unit_price_raw)) <= 0:
                raise ValidationError(f"Row {i}: unit_price must be > 0.")

            variant_name = _derive_variant_name(row)

            product_name = str(row.get("product_name") or "").strip()
            supplier_link = str(row.get("supplier_link") or "").strip()
            variant_code = str(row.get("variant_code") or "").strip()
            if not product_name and not supplier_link and not variant_code:
                raise ValidationError(
                    f"Row {i}: at least one of product_name, supplier_link must be present or variant_code is set."
                )

            discounted_raw = row.get("discounted_price")
            if discounted_raw is not None and str(discounted_raw).strip():
                discounted_price = Decimal(str(discounted_raw))
                if discounted_price >= Decimal(str(unit_price_raw)):
                    row["discounted_price"] = None
                elif discounted_price <= 0:
                    raise ValidationError(f"Row {i}: discounted_price must be > 0.")

            qty_raw = row.get("qty_suggested")
            if qty_raw is not None:
                qty = int(qty_raw)
                if qty < 0:
                    raise ValidationError(f"Row {i}: qty_suggested must be >= 0.")
                row["qty_suggested"] = qty

            row_key = _derive_row_key(row, i, variant_name)
            if row_key in row_keys_seen:
                continue
            row_keys_seen.add(row_key)
            deduped_rows.append(row)

        rows = deduped_rows

        # Category auto-creation (batched)
        codes_to_create = list(
            {
                row["category_code"]
                for row in rows
                if not row.get("category_id") and row.get("category_code")
            }
        )
        if codes_to_create:
            existing_cats = {
                c.category_code: c
                for c in Category.objects.filter(
                    company=po.company, category_code__in=codes_to_create
                )
            }
            missing_codes = [code for code in codes_to_create if code not in existing_cats]
            new_cats = Category.objects.bulk_create(
                [
                    Category(company=po.company, category_code=code, name=code)
                    for code in missing_codes
                ]
            )
            cat_map: dict[str, str] = {}
            for c in existing_cats.values():
                cat_map[c.category_code] = str(c.id)
            for c in new_cats:
                cat_map[c.category_code] = str(c.id)
            for row in rows:
                code = row.get("category_code")
                if code and code in cat_map and not row.get("category_id"):
                    row["category_id"] = cat_map[code]

        # Validate category_ids
        category_ids_raw = {str(row["category_id"]) for row in rows if row.get("category_id")}
        if category_ids_raw:
            _validate_referenced_ids(category_ids_raw, Category, po.company.id, "category_id")

        # Validate variant_ids
        variant_ids_raw = {str(row["variant_id"]) for row in rows if row.get("variant_id")}
        if variant_ids_raw:
            _validate_referenced_ids(variant_ids_raw, ProductVariant, po.company.id, "variant_id")

        # Build SourcingRow objects
        sourcing_rows = [
            _build_sourcing_row(row, i, dim_mismatch_resolutions) for i, row in enumerate(rows)
        ]

        color_map: dict[str, str] = {
            ca.color_name.lower(): ca.abbreviation
            for ca in ColorAbbreviation.objects.filter(company=po.company)
        }

        added: list[dict] = []
        skipped: list[dict] = []
        sku_conflicts: list[dict] = []

        # Process rows with variant_id (pre-linked variants)
        for sr in [r for r in sourcing_rows if r.variant_id]:
            try:
                ordered_qty = sr.qty_suggested or 1
                po_detail = _create_po_detail(po, str(sr.variant_id), sr, ordered_qty)
                _record_added(added, sr, po_detail)
            except IntegrityError:
                logger.warning("Duplicate PO line for variant %s — skipped", sr.variant_id)
                _record_skipped(skipped, sr, "Duplicate PO line")

        remaining = [r for r in sourcing_rows if not r.variant_id]

        # Group by product
        product_groups: dict[tuple, list[SourcingRow]] = {}
        for sr in remaining:
            if sr.supplier_link:
                key: tuple = ("link", sr.supplier_link)
            elif sr.product_name:
                key = ("name", sr.product_name.lower())
            else:
                key = ("standalone", sr.row_key)
            product_groups.setdefault(key, []).append(sr)

        # Build effective variant codes for all sourcing rows
        effective_variant_codes_map: dict[str, str | None] = {}
        for group_items in product_groups.values():
            for sr in group_items:
                if sr.dim_mismatch_resolution == "dims":
                    effective_variant_codes_map[sr.row_key] = None
                else:
                    effective_variant_codes_map[sr.row_key] = sr.variant_code or None

        # Bulk pre-fetch Products and ProductVariants for Track A items
        all_track_a_codes: list[str] = []
        for group_items in product_groups.values():
            for sr in group_items:
                vc = effective_variant_codes_map.get(sr.row_key)
                if vc:
                    parts = vc.split("-")
                    sku_code = "-".join(parts[:2]) if len(parts) >= 2 else vc
                    all_track_a_codes.append(sku_code)

        products_by_sku: dict[str, Product] = {
            p.sku_code: p
            for p in Product.objects.filter(company=po.company, sku_code__in=all_track_a_codes)
        }

        all_variant_codes = [
            effective_variant_codes_map[sr.row_key]
            for grp in product_groups.values()
            for sr in grp
            if effective_variant_codes_map.get(sr.row_key)
        ]

        variants_by_sku_variant_code: dict[str, ProductVariant] = {
            v.sku_variant_code: v
            for v in ProductVariant.objects.filter(
                product__in=products_by_sku.values(),
                sku_variant_code__in=all_variant_codes,
            )
        }

        for group_key, group_items in product_groups.items():
            first = group_items[0]
            product_name_resolved = first.product_name or first.supplier_link or "Unnamed"

            category_id = None
            for sr in group_items:
                if sr.category_id:
                    category_id = sr.category_id
                    break

            track_a_items = [
                sr for sr in group_items if effective_variant_codes_map.get(sr.row_key)
            ]
            track_b_items = [
                sr for sr in group_items if not effective_variant_codes_map.get(sr.row_key)
            ]

            # Track A: rows with effective_variant_code
            for sr in track_a_items:
                variant_code_val = effective_variant_codes_map[sr.row_key]
                if not variant_code_val:
                    continue

                parts = variant_code_val.split("-")
                sku_code = "-".join(parts[:2]) if len(parts) >= 2 else variant_code_val

                try:
                    existing_product = products_by_sku[sku_code]
                    try:
                        existing_variant = variants_by_sku_variant_code[variant_code_val]
                        ordered_qty = sr.qty_suggested or 1
                        po_detail = _create_po_detail(po, existing_variant.id, sr, ordered_qty)
                        _record_added(added, sr, po_detail)
                    except KeyError:
                        sku_conflicts.append(
                            {
                                "row_key": sr.row_key,
                                "row": {
                                    "product_name": sr.product_name,
                                    "variant_name": sr.variant_name,
                                    "variant_code": sr.variant_code,
                                    "variant_id": sr.variant_id,
                                    "category_id": sr.category_id,
                                    "unit_price": str(sr.unit_price),
                                    "discounted_price": str(sr.discounted_price)
                                    if sr.discounted_price
                                    else None,
                                    "qty_suggested": sr.qty_suggested,
                                    "supplier_link": sr.supplier_link,
                                    "dim1_key": sr.dim1_key,
                                    "dim1_value": sr.dim1_value,
                                    "dim2_key": sr.dim2_key,
                                    "dim2_value": sr.dim2_value,
                                },
                                "variant_code": variant_code_val,
                                "sku_code": sku_code,
                                "existing_product_id": str(existing_product.id),
                                "existing_product_name": existing_product.name,
                            }
                        )
                except KeyError:
                    if not category_id:
                        _record_skipped(skipped, sr, "No category assigned")
                        continue

                    dim1_values = [sr.dim1_value] if sr.dim1_value else []
                    dim2_values = [sr.dim2_value] if sr.dim2_value else []

                    try:
                        product = Product.objects.create(
                            company=po.company,
                            category_id=category_id,
                            name=product_name_resolved,
                            sku_code=sku_code,
                            dim1_key=sr.dim1_key or "",
                            dim2_key=sr.dim2_key or "",
                            dim1_options=dim1_values,
                            dim2_options=dim2_values,
                            description="",
                        )
                    except IntegrityError:
                        logger.warning(
                            "Product creation conflict for sku_code %s — skipped", sku_code
                        )
                        _record_skipped(skipped, sr, "Product creation conflict")
                        continue

                    variant_values: dict[str, str] = {}
                    if sr.dim1_key and sr.dim1_value:
                        variant_values[sr.dim1_key] = sr.dim1_value
                    if sr.dim2_key and sr.dim2_value:
                        variant_values[sr.dim2_key] = sr.dim2_value

                    try:
                        variant = ProductVariant.objects.create(
                            product=product,
                            company=po.company,
                            name=_build_variant_name(sr),
                            sku_variant_code=variant_code_val,
                            variant_values=variant_values,
                            base_price=0,
                        )
                        ordered_qty = sr.qty_suggested or 1
                        po_detail = _create_po_detail(po, variant.id, sr, ordered_qty)
                        _record_added(added, sr, po_detail)
                    except IntegrityError:
                        logger.warning(
                            "sku_variant_code collision for %s — skipped", variant_code_val
                        )
                        _record_skipped(skipped, sr, "SKU variant code collision")

            # Track B: rows without effective_variant_code
            if track_b_items:
                if not category_id:
                    for sr in track_b_items:
                        _record_skipped(skipped, sr, "No category assigned")
                    continue

                dim1_values_b = list({sr.dim1_value for sr in track_b_items if sr.dim1_value})
                dim2_values_b = list({sr.dim2_value for sr in track_b_items if sr.dim2_value})

                first_b = track_b_items[0]

                # Check for existing product by name (prevents duplicates on re-submit)
                existing = (
                    Product.objects.select_for_update()
                    .filter(company=po.company, name=product_name_resolved)
                    .first()
                )
                if existing:
                    product_b = existing
                    product_b.refresh_from_db(fields=["sku_code"])
                else:
                    try:
                        product_b = Product.objects.create(
                            company=po.company,
                            category_id=category_id,
                            name=product_name_resolved,
                            dim1_key=first_b.dim1_key or "",
                            dim2_key=first_b.dim2_key or "",
                            dim1_options=dim1_values_b,
                            dim2_options=dim2_values_b,
                            description="",
                        )
                    except IntegrityError:
                        logger.warning(
                            "Product creation conflict for %s — skipped", product_name_resolved
                        )
                        for sr in track_b_items:
                            _record_skipped(skipped, sr, "Product creation conflict")
                        continue
                    product_b.refresh_from_db(fields=["sku_code"])

                for sr in track_b_items:
                    suffix = generate_variant_suffix(
                        sr.dim1_key or "",
                        sr.dim1_value or "",
                        sr.dim2_key or "",
                        sr.dim2_value or "",
                        color_map,
                    )
                    sku_variant_code = (
                        f"{product_b.sku_code}-{suffix}"
                        if suffix
                        else f"{product_b.sku_code}-DEFAULT"
                    )

                    variant_values_b: dict[str, str] = {}
                    if sr.dim1_key and sr.dim1_value:
                        variant_values_b[sr.dim1_key] = sr.dim1_value
                    if sr.dim2_key and sr.dim2_value:
                        variant_values_b[sr.dim2_key] = sr.dim2_value

                    track_b_existing_variant: ProductVariant | None = ProductVariant.objects.filter(
                        product=product_b, sku_variant_code=sku_variant_code
                    ).first()
                    if track_b_existing_variant is not None:
                        variant_b = track_b_existing_variant
                    else:
                        try:
                            variant_b = ProductVariant.objects.create(
                                product=product_b,
                                company=po.company,
                                name=_build_variant_name(sr),
                                sku_variant_code=sku_variant_code,
                                variant_values=variant_values_b,
                                base_price=0,
                            )
                        except IntegrityError:
                            logger.warning(
                                "sku_variant_code collision for %s — skipped", sku_variant_code
                            )
                            _record_skipped(skipped, sr, "SKU variant code collision")
                            continue
                    ordered_qty = sr.qty_suggested or 1
                    po_detail = _create_po_detail(po, variant_b.id, sr, ordered_qty)
                    _record_added(added, sr, po_detail)

        from apps.purchasing.services.purchasing_service import PurchaseOrderService

        PurchaseOrderService()._recalculate_po_totals(po)

        return {
            "added": added,
            "skipped": skipped,
            "sku_conflicts": sku_conflicts,
        }

    @transaction.atomic
    def resolve_sourcing_conflicts(
        self,
        po: PurchaseOrder,
        resolutions: list[dict],
    ) -> dict:
        if po.status not in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
            raise ValidationError(f"Cannot add items to a PO with status {po.status}.")

        color_map: dict[str, str] = {
            ca.color_name.lower(): ca.abbreviation
            for ca in ColorAbbreviation.objects.filter(company=po.company)
        }

        added: list[dict] = []
        skipped: list[dict] = []

        # Bulk pre-fetch products for add_to_existing resolutions
        product_ids = [
            r.get("product_id")
            for r in resolutions
            if r.get("action") == "add_to_existing"
            and r.get("product_id")
            and _is_valid_ulid(r["product_id"])
        ]
        products_map: dict[str, Product] = (
            {
                str(p.id): p
                for p in Product.objects.select_for_update().filter(
                    id__in=product_ids, company=po.company
                )
            }
            if product_ids
            else {}
        )

        for i, resolution in enumerate(resolutions):
            row_data = resolution.get("row", {})
            action = resolution.get("action", "")

            sr = _build_sourcing_row(row_data, i, None)

            if action == "skip":
                _record_skipped(skipped, sr, "Skipped by user")
                continue

            if action == "add_to_existing":
                product_id = resolution.get("product_id")
                if not product_id:
                    raise ValidationError(
                        f"product_id is required for add_to_existing (row_key={sr.row_key})"
                    )
                product = products_map.get(str(product_id))
                if product is None:
                    _record_skipped(skipped, sr, "Product not found")
                    continue

                if sr.variant_code:
                    sku_variant_code = sr.variant_code
                else:
                    suffix = generate_variant_suffix(
                        sr.dim1_key or "",
                        sr.dim1_value or "",
                        sr.dim2_key or "",
                        sr.dim2_value or "",
                        color_map,
                    )
                    sku_variant_code = (
                        f"{product.sku_code}-{suffix}" if suffix else f"{product.sku_code}-DEFAULT"
                    )

                variant_values_r: dict[str, str] = {}
                if sr.dim1_key and sr.dim1_value:
                    variant_values_r[sr.dim1_key] = sr.dim1_value
                if sr.dim2_key and sr.dim2_value:
                    variant_values_r[sr.dim2_key] = sr.dim2_value

                try:
                    variant_r = ProductVariant.objects.create(
                        product=product,
                        company=po.company,
                        name=_build_variant_name(sr),
                        sku_variant_code=sku_variant_code,
                        variant_values=variant_values_r,
                        base_price=0,
                    )
                    ordered_qty = sr.qty_suggested or 1
                    po_detail = _create_po_detail(po, variant_r.id, sr, ordered_qty)
                    _record_added(added, sr, po_detail)
                except IntegrityError:
                    logger.warning(
                        "sku_variant_code collision during resolve: %s — skipped", sku_variant_code
                    )
                    _record_skipped(skipped, sr, "SKU variant code collision")
            else:
                raise ValidationError(f"Unknown action '{action}' for row_key={sr.row_key}")

        from apps.purchasing.services.purchasing_service import PurchaseOrderService

        PurchaseOrderService()._recalculate_po_totals(po)

        return {"added": added, "skipped": skipped}
