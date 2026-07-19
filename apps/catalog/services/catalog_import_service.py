"""
Service layer for importing master catalog data from parsed Excel rows.

All DB writes are wrapped in a single @transaction.atomic call.
No ORM calls inside Python loops — all lookups are done in bulk before loops.
"""

from dataclasses import dataclass

from django.db import transaction

from core.models import Company
from core.parsers.import_master_data_parser import (
    MasterSkuRow,
    VariantRow,
    build_variant_options,
)
from core.utils import get_default_shipping_config

CATEGORY_META: dict[str, tuple[str, int]] = {
    "SEG": ("Set / Setelan", 525),
    "DRS": ("Dress", 516),
    "JNG": ("Jeans / Pants", 517),
    "PNG": ("Pants", 515),
}


@dataclass
class ImportResult:
    suppliers_created: int
    categories_created: int
    products_created: int
    variants_created: int
    warehouse_stock_created: int
    product_suppliers_created: int
    warehouse_name: str


class CatalogImportService:
    @transaction.atomic
    def import_master_data(
        self,
        company: Company,
        master_sku_rows: list[MasterSkuRow],
        variant_rows: list[VariantRow],
        warehouse_name: str = "Master Warehouse",
    ) -> ImportResult:
        from apps.catalog.models import Category, Product, ProductVariant
        from apps.inventory.models import ProductVariantWarehouse, Warehouse
        from apps.purchasing.models import ProductSupplier, Supplier

        # Step 1 — ensure warehouse
        warehouse, _ = Warehouse.objects.get_or_create(
            name=warehouse_name,
            company=company,
            defaults={"is_marketplace_visible": True, "is_active": True},
        )

        # Step 2 — collect all supplier names from both sheets
        all_supplier_names: set[str] = set()
        for msku_row in master_sku_rows:
            if msku_row.supplier_name:
                all_supplier_names.add(msku_row.supplier_name)
        for var_row in variant_rows:
            if var_row.supplier_name:
                all_supplier_names.add(var_row.supplier_name)

        # Bulk fetch existing suppliers by name
        existing_suppliers = Supplier.objects.filter(
            name__in=all_supplier_names, company=company
        ).values("name", "id")
        supplier_map: dict[str, str] = {r["name"]: str(r["id"]) for r in existing_suppliers}

        missing_supplier_names = [n for n in all_supplier_names if n not in supplier_map]
        new_suppliers = [
            Supplier(name=name, company=company, is_active=True) for name in missing_supplier_names
        ]
        created_suppliers = Supplier.objects.bulk_create(new_suppliers, ignore_conflicts=True)
        suppliers_created = len(created_suppliers)

        # Re-fetch to get ids for newly-created suppliers too
        all_supplier_qs = Supplier.objects.filter(
            name__in=all_supplier_names, company=company
        ).values("name", "id")
        supplier_map = {r["name"]: str(r["id"]) for r in all_supplier_qs}

        # Step 3 — collect category codes from master_sku_rows
        category_codes: set[str] = {r.category_code for r in master_sku_rows if r.category_code}

        existing_categories = Category.objects.filter(category_code__in=category_codes).values(
            "category_code", "id"
        )
        cat_map: dict[str, str] = {r["category_code"]: str(r["id"]) for r in existing_categories}

        missing_cat_codes = [c for c in category_codes if c not in cat_map]
        new_categories: list[Category] = []
        for cat_code in missing_cat_codes:
            meta = CATEGORY_META.get(cat_code)
            if not meta:
                continue
            cat_name, shopee_cat_id = meta
            new_categories.append(
                Category(
                    company=company,
                    name=cat_name,
                    category_code=cat_code,
                    shopee_category_id=shopee_cat_id,
                    is_active=True,
                    master_category_key="",
                )
            )
        created_categories = Category.objects.bulk_create(new_categories, ignore_conflicts=True)
        categories_created = len(created_categories)

        # Re-fetch to include newly-created categories
        all_cat_qs = Category.objects.filter(category_code__in=category_codes).values(
            "category_code", "id"
        )
        cat_map = {r["category_code"]: str(r["id"]) for r in all_cat_qs}

        # Step 4 — group variant rows by sku_code for variant_options building
        variants_by_sku: dict[str, list[VariantRow]] = {}
        for vr in variant_rows:
            variants_by_sku.setdefault(vr.sku_code, []).append(vr)

        # Step 5 — bulk-create products
        # sku_code is set explicitly from the Excel; editable=False only affects admin forms
        existing_sku_codes: set[str] = set(
            Product.objects.filter(
                company=company,
                sku_code__in=[r.sku_code for r in master_sku_rows],
            ).values_list("sku_code", flat=True)
        )

        new_products: list[Product] = []
        product_supplier_pairs: list[tuple[str, str]] = []  # (sku_code, supplier_name)

        for row in master_sku_rows:
            if not row.category_code or row.category_code not in cat_map:
                continue
            if row.sku_code in existing_sku_codes:
                continue

            variant_list = variants_by_sku.get(row.sku_code, [])
            variant_options, dim1_options, dim2_options = build_variant_options(variant_list)

            product = Product(
                company=company,
                category_id=cat_map[row.category_code],
                name=row.product_name,
                sku_code=row.sku_code,
                variant_options=variant_options,
                specifications={},
                shipping_config=get_default_shipping_config(),
                weight=0,
                length=0,
                width=0,
                height=0,
                dim1_key="size",
                dim2_key="color",
                dim1_options=dim1_options,
                dim2_options=dim2_options,
                is_active=True,
            )
            new_products.append(product)
            if row.supplier_name:
                product_supplier_pairs.append((row.sku_code, row.supplier_name))

        created_products = Product.objects.bulk_create(new_products, ignore_conflicts=True)
        products_created = len(created_products)

        # Fetch all products (existing + new) for this company that match our sku_codes
        all_sku_codes = [r.sku_code for r in master_sku_rows]
        sku_to_product_id: dict[str, str] = dict(
            Product.objects.filter(company=company, sku_code__in=all_sku_codes).values_list(
                "sku_code", "id"
            )
        )
        # Convert ULID objects to strings
        sku_to_product_id = {k: str(v) for k, v in sku_to_product_id.items()}

        # Step 6 — bulk-create ProductSupplier links
        existing_ps = set(
            ProductSupplier.objects.filter(
                product_id__in=sku_to_product_id.values(), company=company
            ).values_list("product_id", "supplier_id")
        )
        existing_ps_str = {(str(p), str(s)) for p, s in existing_ps}

        new_ps: list[ProductSupplier] = []
        for sku_code, supplier_name in product_supplier_pairs:
            product_id = sku_to_product_id.get(sku_code)
            supplier_id = supplier_map.get(supplier_name)
            if not product_id or not supplier_id:
                continue
            if (product_id, supplier_id) in existing_ps_str:
                continue
            new_ps.append(
                ProductSupplier(
                    company=company,
                    product_id=product_id,
                    supplier_id=supplier_id,
                    supplier_link=None,
                )
            )
        created_ps = ProductSupplier.objects.bulk_create(new_ps, ignore_conflicts=True)
        product_suppliers_created = len(created_ps)

        # Step 7 — bulk-create ProductVariant rows
        existing_variant_codes: set[str] = set(
            ProductVariant.objects.filter(
                company=company,
                sku_variant_code__in=[vr.sku_variant_code for vr in variant_rows],
            ).values_list("sku_variant_code", flat=True)
        )

        new_variants: list[ProductVariant] = []
        for vr in variant_rows:
            product_id = sku_to_product_id.get(vr.sku_code)
            if not product_id:
                continue
            if vr.sku_variant_code in existing_variant_codes:
                continue
            variant_name = (
                f"{vr.size_code} / {vr.color_display}"
                if vr.size_code or vr.color_display
                else "Default"
            )
            variant_values = {"size": vr.size_code, "color": vr.color_code}
            new_variants.append(
                ProductVariant(
                    company=company,
                    product_id=product_id,
                    name=variant_name,
                    sku_variant_code=vr.sku_variant_code,
                    variant_values=variant_values,
                    current_cogs=int(vr.cogs),
                    base_price=int(vr.base_price),
                    total_incoming_qty=vr.stock_qty,
                    total_outgoing_qty=0,
                    total_available_qty=vr.stock_qty,
                    is_active=True,
                    is_fake=False,
                )
            )

        created_variants = ProductVariant.objects.bulk_create(new_variants, ignore_conflicts=True)
        variants_created = len(created_variants)

        # Step 8 — bulk-create ProductVariantWarehouse stock rows
        # Fetch all variant ids by sku_variant_code for the rows we care about
        variant_code_to_id: dict[str, str] = dict(
            ProductVariant.objects.filter(
                company=company,
                sku_variant_code__in=[vr.sku_variant_code for vr in variant_rows],
            ).values_list("sku_variant_code", "id")
        )
        variant_code_to_id = {k: str(v) for k, v in variant_code_to_id.items()}

        existing_pvw = set(
            ProductVariantWarehouse.objects.filter(
                product_variant_id__in=variant_code_to_id.values(),
                warehouse=warehouse,
            ).values_list("product_variant_id", flat=True)
        )
        existing_pvw_str = {str(v) for v in existing_pvw}

        new_pvw: list[ProductVariantWarehouse] = []
        for vr in variant_rows:
            variant_id = variant_code_to_id.get(vr.sku_variant_code)
            if not variant_id:
                continue
            if variant_id in existing_pvw_str:
                continue
            new_pvw.append(
                ProductVariantWarehouse(
                    company=company,
                    product_variant_id=variant_id,
                    warehouse=warehouse,
                    incoming_qty=vr.stock_qty,
                    outgoing_qty=0,
                    physical_qty=vr.stock_qty,
                    checkout_qty=0,
                )
            )

        created_pvw = ProductVariantWarehouse.objects.bulk_create(new_pvw, ignore_conflicts=True)
        warehouse_stock_created = len(created_pvw)

        return ImportResult(
            suppliers_created=suppliers_created,
            categories_created=categories_created,
            products_created=products_created,
            variants_created=variants_created,
            warehouse_stock_created=warehouse_stock_created,
            product_suppliers_created=product_suppliers_created,
            warehouse_name=warehouse_name,
        )
