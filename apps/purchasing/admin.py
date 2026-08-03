from django.contrib import admin

from apps.purchasing.models import (
    ColorAbbreviation,
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderStatusHistory,
    Supplier,
)

admin.site.register(PurchaseOrder)
admin.site.register(PurchaseOrderStatusHistory)


@admin.register(ColorAbbreviation)
class ColorAbbreviationAdmin(admin.ModelAdmin):
    list_display = ["color_name", "abbreviation", "company"]
    list_filter = ["company"]
    search_fields = ["color_name", "abbreviation"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "contact_name", "country", "is_active"]
    list_filter = ["is_active", "country"]
    search_fields = ["name", "contact_name"]


@admin.register(ProductSupplier)
class ProductSupplierAdmin(admin.ModelAdmin):
    list_display = ["product", "supplier", "company"]
