from django.contrib import admin

from apps.purchasing.models import PurchaseOrder, PurchaseOrderStatusHistory

admin.site.register(PurchaseOrder)
admin.site.register(PurchaseOrderStatusHistory)
