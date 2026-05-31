from typing import Any, Type

from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from apps.sales.models import SalesOrder, SalesReturn
from apps.sales.serializers import (
    SalesOrderCreateSerializer,
    SalesOrderDetailSerializer,
    SalesOrderListSerializer,
    SalesOrderUpdateSerializer,
    SalesReturnCreateSerializer,
    SalesReturnSerializer,
)
from apps.sales.services.sales_service import SalesOrderService, SalesReturnService
from core.permissions import IsStaffOrReadOnly


class SalesOrderViewSet(viewsets.ModelViewSet):
    queryset = SalesOrder.objects.none()
    http_method_names = ["get", "post", "patch"]
    permission_classes = [IsStaffOrReadOnly]

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action == "create":
            return SalesOrderCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return SalesOrderUpdateSerializer
        elif self.action == "list":
            return SalesOrderListSerializer
        else:
            return SalesOrderDetailSerializer

    def get_queryset(self) -> QuerySet[SalesOrder]:
        if not self.request.user.is_authenticated:
            return SalesOrder.objects.none()
        qs = SalesOrder.objects.filter(company=self.request.user.profile.company)
        status_filter = self.request.query_params.get("status")
        source_platform_filter = self.request.query_params.get("source_platform")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if source_platform_filter:
            qs = qs.filter(source_platform=source_platform_filter)
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(order_date__date__gte=date_from)
        if date_to:
            qs = qs.filter(order_date__date__lte=date_to)
        return qs  # type: ignore[no-any-return]

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            so = serializer.save()
            return Response(SalesOrderDetailSerializer(so).data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        serializer = SalesOrderDetailSerializer(instance)
        return Response(serializer.data)

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            service = SalesOrderService()
            so = service.update_sales_order(instance, serializer.validated_data)
            return Response(SalesOrderDetailSerializer(so).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def confirm(self, request: Request, pk: Any = None) -> Response:
        so = self.get_object()
        try:
            service = SalesOrderService()
            so = service.confirm_order(so)
            return Response(SalesOrderDetailSerializer(so).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: Any = None) -> Response:
        so = self.get_object()
        try:
            service = SalesOrderService()
            so = service.cancel_order(so)
            return Response(SalesOrderDetailSerializer(so).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class SalesReturnViewSet(viewsets.ModelViewSet):
    queryset = SalesReturn.objects.none()
    http_method_names = ["get", "post", "patch"]
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet:
        if not self.request.user.is_authenticated:
            return SalesReturn.objects.none()
        return SalesReturn.objects.filter(company=self.request.user.profile.company)

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action == "create":
            return SalesReturnCreateSerializer
        return SalesReturnSerializer

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = SalesReturnSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = SalesReturnSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        serializer = SalesReturnSerializer(instance)
        return Response(serializer.data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            sales_order = SalesOrder.objects.get(
                id=serializer.validated_data["sales_order_id"],
                company=request.user.profile.company,
            )
            service = SalesReturnService()
            sales_return = service.create_return(
                sales_order,
                {
                    "reason": serializer.validated_data.get("reason", ""),
                    "refund_amount": serializer.validated_data.get("refund_amount", 0),
                    "note": serializer.validated_data.get("note", ""),
                    "items": serializer.validated_data["items"],
                },
            )
            return Response(
                SalesReturnSerializer(sales_return).data, status=status.HTTP_201_CREATED
            )
        except SalesOrder.DoesNotExist:
            return Response({"error": "Sales order not found."}, status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def receive(self, request: Request, pk: Any = None) -> Response:
        sales_return = self.get_object()
        try:
            service = SalesReturnService()
            sales_return = service.receive_return(sales_return)
            return Response(SalesReturnSerializer(sales_return).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
