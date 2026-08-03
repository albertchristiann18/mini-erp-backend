from django.contrib import admin

from apps.marketplace.models import (
    BusinessEntity,
    CompanyMarketplace,
    Marketplace,
    MarketplaceConnection,
    ProductBusinessEntity,
)


@admin.register(Marketplace)
class MarketplaceAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]


@admin.register(MarketplaceConnection)
class MarketplaceConnectionAdmin(admin.ModelAdmin):
    list_display = ["company", "platform", "display_name", "is_active"]
    list_filter = ["platform", "is_active"]


@admin.register(CompanyMarketplace)
class CompanyMarketplaceAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "is_active"]
    list_filter = ["is_active"]


@admin.register(BusinessEntity)
class BusinessEntityAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "marketplace", "is_active"]
    list_filter = ["marketplace", "is_active"]


@admin.register(ProductBusinessEntity)
class ProductBusinessEntityAdmin(admin.ModelAdmin):
    list_display = ["product", "business_entity"]
