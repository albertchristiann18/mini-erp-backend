from django.contrib import admin

from apps.purchasing.models import ColorAbbreviation, PurchaseOrder, PurchaseOrderStatusHistory

admin.site.register(PurchaseOrder)
admin.site.register(PurchaseOrderStatusHistory)


@admin.register(ColorAbbreviation)
class ColorAbbreviationAdmin(admin.ModelAdmin):
    list_display = ["color_name", "abbreviation", "company"]
    list_filter = ["company"]
    search_fields = ["color_name", "abbreviation"]
