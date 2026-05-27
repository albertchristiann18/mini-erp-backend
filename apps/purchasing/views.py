from typing import Any, Type

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from apps.purchasing.models import PurchaseOrder
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
