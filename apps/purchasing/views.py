from typing import Any, Type

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.purchasing.models import PurchaseOrder, PurchaseOrderDetail
from apps.purchasing.serializers import (
    PurchaseOrderCreateSerializer,
    PurchaseOrderListSerializer,
    PurchaseOrderReadSerializer,
    PurchaseOrderUpdateSerializer,
)
from apps.purchasing.services import purchasing_service
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.permissions import IsStaffOrReadOnly


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """
    API endpoints for Purchase Orders.
    - GET /purchase-orders/ - List all purchase orders
    - POST /purchase-orders/ - Create purchase order with nested details
    - GET /purchase-orders/{id}/ - Get purchase order details
    - PUT/PATCH /purchase-orders/{id}/ - Update purchase order and details
    """

    queryset = PurchaseOrder.objects.all()
    http_method_names = ["get", "post", "put", "patch"]
    permission_classes = [IsStaffOrReadOnly]

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action == "create":
            return PurchaseOrderCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return PurchaseOrderUpdateSerializer
        elif self.action == "list":
            return PurchaseOrderListSerializer
        else:  # retrieve
            return PurchaseOrderReadSerializer

    def get_queryset(self) -> QuerySet[PurchaseOrder]:
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        forwarder = self.request.query_params.get("forwarder")
        if date_from:
            qs = qs.filter(invoice_date__gte=date_from)
        if date_to:
            qs = qs.filter(invoice_date__lte=date_to)
        if forwarder:
            qs = qs.filter(forwarder_name__icontains=forwarder)
        return qs  # type: ignore[no-any-return]

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Get list of all Purchase Orders (basic info without details)"""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Create a new Purchase Order with nested details"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            services = purchasing_service.PurchaseOrderService()
            services.create_purchase_order(serializer.validated_data)
            return Response(status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Get a Purchase Order with all details"""
        instance = self.get_object()
        serializer = PurchaseOrderReadSerializer(instance)
        return Response(serializer.data)

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Update Purchase Order and details"""
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=kwargs.pop("partial", False)
        )
        serializer.is_valid(raise_exception=True)

        try:
            validated_data = serializer.validated_data
            services = purchasing_service.PurchaseOrderService()
            services.update_purchase_order(instance, validated_data)

            return Response(status=status.HTTP_200_OK)

        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def advance_status(self, request: Request, pk: Any = None) -> Response:
        """POST /purchase-order/{id}/advance_status/ with body {"status": "ORDERED"}"""
        po = self.get_object()
        new_status = request.data.get("status")
        try:
            service = PurchaseOrderService()
            po = service.update_purchase_order(po, {"status": new_status})
            return Response(PurchaseOrderReadSerializer(po).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ReplenishmentView(APIView):
    """
    GET /api/purchasing/replenishment/
    Returns per-variant stock health for replenishment planning.

    Query params:
      - warehouse_id: optional — filter SOH to a specific warehouse.
                      If omitted, aggregates across all warehouses.

    Response per variant:
      {
        variant_id, sku_variant_code, variant_name, product_name,
        stock_on_hand, incoming_qty,
        avg_sales_7d, avg_sales_30d
      }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        from apps.inventory.models import ProductVariant, ProductVariantWarehouse
        from apps.inventory.services.inventory_service import InventoryService

        warehouse_id = request.query_params.get("warehouse_id")

        # SOH per variant
        pvw_qs = ProductVariantWarehouse.objects.filter(
            product_variant__is_active=True,
        )
        if warehouse_id:
            pvw_qs = pvw_qs.filter(warehouse_id=warehouse_id)

        soh_map: dict[str, int] = {}
        for pvw in pvw_qs.values("product_variant_id", "physical_qty"):
            vid = str(pvw["product_variant_id"])
            soh_map[vid] = soh_map.get(vid, 0) + (pvw["physical_qty"] or 0)

        # Incoming qty per variant (ORDERED + SHIPPED POs)
        open_statuses = [
            PurchaseOrder.POStatus.ORDERED,
            PurchaseOrder.POStatus.SHIPPED,
        ]
        detail_qs = PurchaseOrderDetail.objects.filter(
            purchase_order__status__in=open_statuses,
            product_variant__is_active=True,
        )
        if warehouse_id:
            detail_qs = detail_qs.filter(purchase_order__warehouse_id=warehouse_id)

        incoming_map: dict[str, int] = {}
        for detail in detail_qs.values("product_variant_id", "ordered_qty", "received_qty"):
            vid = str(detail["product_variant_id"])
            gap = max(0, (detail["ordered_qty"] or 0) - (detail["received_qty"] or 0))
            incoming_map[vid] = incoming_map.get(vid, 0) + gap

        # All active variant IDs in scope
        all_ids = sorted(set(list(soh_map.keys()) + list(incoming_map.keys())))
        if not all_ids:
            all_ids = list(
                ProductVariant.objects.filter(is_active=True).values_list("id", flat=True)
            )
            all_ids = [str(v) for v in all_ids]

        if not all_ids:
            return Response({"results": []})

        # Avg sales (7d + 30d)
        svc = InventoryService()
        avg7 = svc.get_avg_sales_per_day(variant_ids=all_ids, days=7)
        avg30 = svc.get_avg_sales_per_day(variant_ids=all_ids, days=30)

        avg7_map = {r["variant_id"]: r["avg_sales_per_day"] for r in avg7}
        avg30_map = {r["variant_id"]: r["avg_sales_per_day"] for r in avg30}

        # Variant metadata
        variants = ProductVariant.objects.filter(id__in=all_ids).select_related("product")

        results = []
        for v in variants:
            vid = str(v.id)
            results.append(
                {
                    "variant_id": vid,
                    "sku_variant_code": v.sku_variant_code,
                    "variant_name": v.name,
                    "product_name": v.product.name,
                    "stock_on_hand": soh_map.get(vid, 0),
                    "incoming_qty": incoming_map.get(vid, 0),
                    "avg_sales_7d": avg7_map.get(vid, 0.0),
                    "avg_sales_30d": avg30_map.get(vid, 0.0),
                }
            )

        results.sort(key=lambda r: r["stock_on_hand"])

        return Response({"results": results})
