from django.db import transaction

DEFAULT_MARKETPLACES = ["Shopee", "TikTok"]


class BusinessEntityService:
    def get_or_seed_company_marketplaces(self, company_id: str) -> list:
        """
        Returns all CompanyMarketplace records for the company.
        If none exist yet, seeds Shopee and TikTok as defaults.
        """
        from apps.inventory.models import CompanyMarketplace
        from core.models import Company

        qs = CompanyMarketplace.objects.filter(company_id=company_id)
        if not qs.exists():
            company = Company.objects.get(id=company_id)
            CompanyMarketplace.objects.bulk_create(
                [
                    CompanyMarketplace(company=company, name=name, is_active=True)
                    for name in DEFAULT_MARKETPLACES
                ]
            )
            qs = CompanyMarketplace.objects.filter(company_id=company_id)
        return list(qs.order_by("name"))

    @transaction.atomic
    def attach_product(self, product_id: str, business_entity_id: str, company_id: str) -> dict:
        from apps.catalog.models import Product
        from apps.inventory.models import BusinessEntity, ProductBusinessEntity

        product = Product.objects.get(id=product_id, company_id=company_id)
        business_entity = BusinessEntity.objects.select_related("marketplace").get(
            id=business_entity_id, company_id=company_id
        )

        # Marketplace conflict check (exclude the same business_entity for idempotency)
        conflict = (
            ProductBusinessEntity.objects.filter(
                product=product,
                business_entity__marketplace=business_entity.marketplace,
            )
            .exclude(business_entity=business_entity)
            .select_related("business_entity")
            .first()
        )

        if conflict:
            raise ValueError(
                f"Product '{product.sku_code}' is already attached to "
                f"'{conflict.business_entity.name}' which uses the same marketplace "
                f"({business_entity.marketplace.name}). "
                f"A product can only be attached to one business entity per marketplace."
            )

        obj, created = ProductBusinessEntity.objects.get_or_create(
            product=product,
            business_entity=business_entity,
            defaults={"company": product.company},
        )
        return {
            "id": str(obj.id),
            "product_id": str(product.id),
            "business_entity_id": str(business_entity.id),
            "created": created,
        }

    @transaction.atomic
    def detach_product(self, product_business_entity_id: str, company_id: str) -> None:
        from apps.inventory.models import ProductBusinessEntity

        try:
            obj = ProductBusinessEntity.objects.get(
                id=product_business_entity_id, company_id=company_id
            )
        except ProductBusinessEntity.DoesNotExist:
            raise ValueError("Assignment not found")
        obj.delete()
