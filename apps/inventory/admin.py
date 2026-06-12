from django.contrib import admin

from apps.inventory.models import BusinessEntity, CompanyMarketplace, ProductBusinessEntity


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
