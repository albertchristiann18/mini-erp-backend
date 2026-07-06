import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventory.models import Product, ProductVariant
from apps.purchasing.models import (
    ColorAbbreviation,
    PurchaseOrder,
    PurchaseOrderDetail,
    SourcingPoolItem,
)
from apps.purchasing.services.sourcing_service import generate_variant_suffix

logger = logging.getLogger(__name__)


def _build_variant_name_from_item(item: SourcingPoolItem) -> str:
    parts = [v for v in [item.dim1_value, item.dim2_value] if v]
    return " / ".join(parts) or "Default"


def _create_po_detail(
    po: PurchaseOrder,
    variant_id: object,
    pool_item: SourcingPoolItem,
    ordered_qty: int,
) -> PurchaseOrderDetail:
    exchange_rate = Decimal(str(po.exchange_rate or 0))
    unit_price_foreign = pool_item.unit_price
    discounted_price_foreign = pool_item.discounted_price or unit_price_foreign

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
        supplier_link=pool_item.supplier_link,
    )


def _mark_item_used(item: SourcingPoolItem, po: PurchaseOrder) -> None:
    item.is_used = True
    item.used_at = timezone.now()
    item.used_in_po = po
    item.save(update_fields=["is_used", "used_at", "used_in_po"])


class SourcingProductService:
    @transaction.atomic
    def add_pool_items_to_po(
        self,
        po: PurchaseOrder,
        item_ids: list[str],
        product_name_overrides: dict[str, str] | None = None,
        dim_mismatch_resolutions: dict[str, str] | None = None,
    ) -> dict:
        if po.status not in [PurchaseOrder.POStatus.DRAFT, PurchaseOrder.POStatus.ORDERED]:
            raise ValidationError(f"Cannot add items to a PO with status {po.status}.")

        product_name_overrides = product_name_overrides or {}
        dim_mismatch_resolutions = dim_mismatch_resolutions or {}

        items = list(
            SourcingPoolItem.objects.select_for_update().filter(
                id__in=item_ids,
                company=po.company,
                is_used=False,
                is_active=True,
            )
        )
        found_ids = {str(i.id) for i in items}
        missing = [iid for iid in item_ids if iid not in found_ids]
        if missing:
            raise ValidationError(f"Pool items not found, already used, or inactive: {missing}")

        color_map: dict[str, str] = {
            ca.color_name.lower(): ca.abbreviation
            for ca in ColorAbbreviation.objects.filter(company=po.company)
        }

        added: list[str] = []
        sku_conflicts: list[dict] = []
        skipped: list[str] = []

        for item in [i for i in items if i.variant_id]:  # type: ignore[attr-defined]
            try:
                ordered_qty = item.qty_suggested or 1
                _create_po_detail(po, item.variant_id, item, ordered_qty)  # type: ignore[attr-defined]
                _mark_item_used(item, po)
                added.append(str(item.id))
            except IntegrityError:
                logger.warning("Duplicate PO line for variant %s — skipped", item.variant_id)  # type: ignore[attr-defined]
                skipped.append(str(item.id))

        remaining = [i for i in items if not i.variant_id]  # type: ignore[attr-defined]

        effective_variant_codes: dict[str, str | None] = {}
        for item in remaining:
            resolution = dim_mismatch_resolutions.get(str(item.id))
            if resolution == "dims":
                effective_variant_codes[str(item.id)] = None
            else:
                effective_variant_codes[str(item.id)] = item.variant_code or None

        product_groups: dict[tuple, list[SourcingPoolItem]] = {}
        for item in remaining:
            product_name = product_name_overrides.get(str(item.id)) or item.product_name
            if item.supplier_link:
                key: tuple = ("link", item.supplier_link)
            elif product_name:
                key = ("name", product_name.lower())
            else:
                key = ("standalone", str(item.id))
            product_groups.setdefault(key, []).append(item)

        for group_key, group_items in product_groups.items():
            first = group_items[0]
            product_name_resolved = (
                product_name_overrides.get(str(first.id))
                or first.product_name
                or first.supplier_link
                or "Unnamed"
            )

            category_id = None
            for item in group_items:
                if item.category_id:  # type: ignore[attr-defined]
                    category_id = item.category_id  # type: ignore[attr-defined]
                    break

            track_a_items = [
                item for item in group_items if effective_variant_codes.get(str(item.id))
            ]
            track_b_items = [
                item for item in group_items if not effective_variant_codes.get(str(item.id))
            ]

            for item in track_a_items:
                variant_code = effective_variant_codes[str(item.id)]
                assert variant_code

                parts = variant_code.split("-")
                sku_code = "-".join(parts[:2]) if len(parts) >= 2 else variant_code

                try:
                    existing_product = Product.objects.get(company=po.company, sku_code=sku_code)
                    try:
                        existing_variant = ProductVariant.objects.get(
                            product=existing_product, sku_variant_code=variant_code
                        )
                        ordered_qty = item.qty_suggested or 1
                        _create_po_detail(po, existing_variant.id, item, ordered_qty)
                        _mark_item_used(item, po)
                        added.append(str(item.id))
                    except ProductVariant.DoesNotExist:
                        sku_conflicts.append(
                            {
                                "item_id": str(item.id),
                                "variant_code": variant_code,
                                "sku_code": sku_code,
                                "existing_product_id": str(existing_product.id),
                                "existing_product_name": existing_product.name,
                            }
                        )
                except Product.DoesNotExist:
                    if not category_id:
                        skipped.append(str(item.id))
                        continue

                    dim1_values = [item.dim1_value] if item.dim1_value else []
                    dim2_values = [item.dim2_value] if item.dim2_value else []

                    product = Product.objects.create(
                        company=po.company,
                        category_id=category_id,
                        name=product_name_resolved,
                        sku_code=sku_code,
                        dim1_key=item.dim1_key or "",
                        dim2_key=item.dim2_key or "",
                        dim1_options=dim1_values,
                        dim2_options=dim2_values,
                        description="",
                    )

                    variant_values: dict[str, str] = {}
                    if item.dim1_key and item.dim1_value:
                        variant_values[item.dim1_key] = item.dim1_value
                    if item.dim2_key and item.dim2_value:
                        variant_values[item.dim2_key] = item.dim2_value

                    try:
                        variant = ProductVariant.objects.create(
                            product=product,
                            company=po.company,
                            name=_build_variant_name_from_item(item),
                            sku_variant_code=variant_code,
                            variant_values=variant_values,
                            base_price=0,
                        )
                        ordered_qty = item.qty_suggested or 1
                        _create_po_detail(po, variant.id, item, ordered_qty)
                        _mark_item_used(item, po)
                        added.append(str(item.id))
                    except IntegrityError:
                        logger.warning("sku_variant_code collision for %s — skipped", variant_code)
                        skipped.append(str(item.id))

            if track_b_items:
                if not category_id:
                    skipped.extend([str(i.id) for i in track_b_items])
                    continue

                dim1_values_b = list({item.dim1_value for item in track_b_items if item.dim1_value})
                dim2_values_b = list({item.dim2_value for item in track_b_items if item.dim2_value})

                first_b = track_b_items[0]
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
                product_b.refresh_from_db(fields=["sku_code"])

                for item in track_b_items:
                    suffix = generate_variant_suffix(
                        item.dim1_key or "",
                        item.dim1_value or "",
                        item.dim2_key or "",
                        item.dim2_value or "",
                        color_map,
                    )
                    sku_variant_code = (
                        f"{product_b.sku_code}-{suffix}"
                        if suffix
                        else f"{product_b.sku_code}-DEFAULT"
                    )

                    variant_values_b: dict[str, str] = {}
                    if item.dim1_key and item.dim1_value:
                        variant_values_b[item.dim1_key] = item.dim1_value
                    if item.dim2_key and item.dim2_value:
                        variant_values_b[item.dim2_key] = item.dim2_value

                    try:
                        variant_b = ProductVariant.objects.create(
                            product=product_b,
                            company=po.company,
                            name=_build_variant_name_from_item(item),
                            sku_variant_code=sku_variant_code,
                            variant_values=variant_values_b,
                            base_price=0,
                        )
                        ordered_qty = item.qty_suggested or 1
                        _create_po_detail(po, variant_b.id, item, ordered_qty)
                        _mark_item_used(item, po)
                        added.append(str(item.id))
                    except IntegrityError:
                        logger.warning(
                            "sku_variant_code collision for %s — skipped", sku_variant_code
                        )
                        skipped.append(str(item.id))

        from apps.purchasing.services.purchasing_service import PurchaseOrderService

        PurchaseOrderService()._recalculate_po_totals(po)

        return {
            "added": added,
            "sku_conflicts": sku_conflicts,
            "skipped": skipped,
        }

    @transaction.atomic
    def resolve_sku_conflicts(
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

        added: list[str] = []
        skipped: list[str] = []

        for resolution in resolutions:
            item_id = resolution.get("item_id", "")
            action = resolution.get("action", "")

            try:
                item = SourcingPoolItem.objects.select_for_update().get(
                    id=item_id, company=po.company, is_used=False
                )
            except SourcingPoolItem.DoesNotExist:
                skipped.append(item_id)
                continue

            if action == "skip":
                skipped.append(str(item.id))
                continue

            if action == "add_to_existing":
                product_id = resolution.get("product_id")
                if not product_id:
                    raise ValidationError(
                        f"product_id is required for add_to_existing action (item_id={item_id})"
                    )
                try:
                    product = Product.objects.select_for_update().get(
                        id=product_id, company=po.company
                    )
                except Product.DoesNotExist:
                    skipped.append(str(item.id))
                    continue

                if item.variant_code:
                    sku_variant_code = item.variant_code
                else:
                    suffix = generate_variant_suffix(
                        item.dim1_key or "",
                        item.dim1_value or "",
                        item.dim2_key or "",
                        item.dim2_value or "",
                        color_map,
                    )
                    sku_variant_code = (
                        f"{product.sku_code}-{suffix}" if suffix else f"{product.sku_code}-DEFAULT"
                    )

                variant_values_r: dict[str, str] = {}
                if item.dim1_key and item.dim1_value:
                    variant_values_r[item.dim1_key] = item.dim1_value
                if item.dim2_key and item.dim2_value:
                    variant_values_r[item.dim2_key] = item.dim2_value

                try:
                    variant_r = ProductVariant.objects.create(
                        product=product,
                        company=po.company,
                        name=_build_variant_name_from_item(item),
                        sku_variant_code=sku_variant_code,
                        variant_values=variant_values_r,
                        base_price=0,
                    )
                    ordered_qty = item.qty_suggested or 1
                    _create_po_detail(po, variant_r.id, item, ordered_qty)
                    _mark_item_used(item, po)
                    added.append(str(item.id))
                except IntegrityError:
                    logger.warning(
                        "sku_variant_code collision during resolve: %s — skipped", sku_variant_code
                    )
                    skipped.append(str(item.id))
            else:
                raise ValidationError(f"Unknown action '{action}' for item_id={item_id}")

        from apps.purchasing.services.purchasing_service import PurchaseOrderService

        PurchaseOrderService()._recalculate_po_totals(po)

        return {"added": added, "skipped": skipped}
