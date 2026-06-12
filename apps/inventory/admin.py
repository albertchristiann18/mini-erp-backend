from django.contrib import admin

from apps.inventory.models import BusinessEntity, ProductBusinessEntity


@admin.register(BusinessEntity)
class BusinessEntityAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "marketplace", "is_active"]
    list_filter = ["marketplace", "is_active"]


@admin.register(ProductBusinessEntity)
class ProductBusinessEntityAdmin(admin.ModelAdmin):
    list_display = ["product", "business_entity"]
