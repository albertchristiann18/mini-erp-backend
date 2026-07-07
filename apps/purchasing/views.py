import logging
from typing import Any, Type

from django.core.exceptions import ValidationError
from django.db.models import Count, Q, QuerySet, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.purchasing.models import (
    PurchaseOrder,
    PurchaseOrderDetail,
)
from apps.purchasing.serializers import (
    PurchaseOrderCreateSerializer,
    PurchaseOrderListSerializer,
    PurchaseOrderReadSerializer,
    PurchaseOrderUpdateSerializer,
)
from apps.purchasing.services import purchasing_service
from apps.purchasing.services.purchasing_service import PurchaseOrderService
from core.permissions import IsStaffOrReadOnly

logger = logging.getLogger(__name__)


class PurchaseOrderPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """
    API endpoints for Purchase Orders.
    - GET /purchase-orders/ - List all purchase orders
    - POST /purchase-orders/ - Create purchase order with nested details
    - GET /purchase-orders/{id}/ - Get purchase order details
    - PUT/PATCH /purchase-orders/{id}/ - Update purchase order and details
    """

    queryset = PurchaseOrder.objects.none()
    http_method_names = ["get", "post", "put", "patch"]
    permission_classes = [IsStaffOrReadOnly]
    pagination_class = PurchaseOrderPagination
    filter_backends = [OrderingFilter]
    ordering_fields = [
        "purchase_order_number",
        "invoice_date",
        "delivery_date",
        "forecast_delivery_date",
        "total_amount",
        "total_ordered_qty",
        "cdate",
    ]
    ordering = ["-cdate"]

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
        if not self.request.user.is_authenticated:
            return PurchaseOrder.objects.none()
        qs = PurchaseOrder.objects.filter(company=self.request.user.profile.company)
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
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(purchase_order_number__icontains=search)
                | Q(invoice_number__icontains=search)
                | Q(delivery_order_number__icontains=search)
            )
        return qs.prefetch_related(  # type: ignore[no-any-return]
            "order_details__product_variant",
            "order_details__product_variant__product",
            "order_details__product_variant__product__photos",
            "order_details__product_variant__product__dimension_images",
        )

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
        data = request.data.copy()
        data["company_id"] = str(request.user.profile.company.id)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        try:
            services = purchasing_service.PurchaseOrderService()
            po = services.create_purchase_order(serializer.validated_data)
            return Response({"id": str(po.id)}, status=status.HTTP_201_CREATED)
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
            services.update_purchase_order(instance, validated_data, changed_by=request.user)

            compressed_fields: list[str] = getattr(serializer, "_compressed_fields", [])
            body = {"compressed_files": compressed_fields} if compressed_fields else {}
            return Response(body, status=status.HTTP_200_OK)

        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def advance_status(self, request: Request, pk: Any = None) -> Response:
        """POST /purchase-order/{id}/advance_status/ with body {"status": "ORDERED"}"""
        po = self.get_object()
        new_status = request.data.get("status")
        if not new_status:
            return Response({"error": "status is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not po.can_advance_to(new_status):
            return Response(
                {"error": f"Cannot transition from {po.status} to {new_status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Field-completeness check — serializer does not run on this path
        service = PurchaseOrderService()
        missing = service.check_purchase_order_requirements(po, new_status)
        if missing:
            return Response(
                {
                    "error": "Required fields are missing for this transition.",
                    "missing_fields": missing,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            po = service.update_purchase_order(po, {"status": new_status}, changed_by=request.user)
            return Response(PurchaseOrderReadSerializer(po).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path="check_transition")
    def check_transition(self, request: Request, pk: Any = None) -> Response:
        """
        POST /purchase-order/{id}/check_transition/
        Body: {"status": "SHIPPED"}
        Returns all missing requirements without performing the transition.
        Used by frontend confirmation modal to preview what needs to be filled.
        """
        po = self.get_object()
        target_status = request.data.get("status")
        if not target_status:
            return Response({"error": "status is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not po.can_advance_to(target_status):
            return Response(
                {
                    "can_transition": False,
                    "target_status": target_status,
                    "missing_fields": [],
                    "error": f"Cannot transition from {po.status} to {target_status}.",
                }
            )

        service = PurchaseOrderService()
        missing = service.check_purchase_order_requirements(po, target_status)
        warnings = service.get_transition_warnings(po, target_status)
        return Response(
            {
                "can_transition": len(missing) == 0,
                "target_status": target_status,
                "missing_fields": missing,
                "warnings": warnings,
            }
        )

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request: Request) -> Response:
        """
        Returns aggregate totals for upcoming POs (ORDERED + SHIPPED).
        Applies date_from, date_to, forwarder filters from query params.
        Status filter is ignored — always scoped to ORDERED + SHIPPED.
        """
        qs = PurchaseOrder.objects.filter(
            company=request.user.profile.company,
            status__in=[PurchaseOrder.POStatus.ORDERED, PurchaseOrder.POStatus.SHIPPED],
        )
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        forwarder = request.query_params.get("forwarder")
        if date_from:
            qs = qs.filter(invoice_date__gte=date_from)
        if date_to:
            qs = qs.filter(invoice_date__lte=date_to)
        if forwarder:
            qs = qs.filter(forwarder_name__icontains=forwarder)

        agg = qs.aggregate(
            upcoming_count=Count("id"),
            upcoming_total_amount=Sum("total_amount"),
            upcoming_total_item_amount=Sum("total_item_amount"),
            upcoming_procure_amount=Sum("procure_amount"),
        )

        return Response(
            {
                "upcoming_count": agg["upcoming_count"] or 0,
                "upcoming_total_amount": agg["upcoming_total_amount"] or 0,
                "upcoming_total_item_amount": agg["upcoming_total_item_amount"] or 0,
                "upcoming_procure_amount": agg["upcoming_procure_amount"] or 0,
            }
        )

    @action(detail=True, methods=["post"], url_path="import-and-add")
    def import_and_add(self, request: Request, pk: Any = None) -> Response:
        from apps.purchasing.services.sourcing_product_service import SourcingProductService

        po = self.get_object()
        supplier_id = request.data.get("supplier_id")
        rows = request.data.get("rows")
        dim_mismatch_resolutions = request.data.get("dim_mismatch_resolutions") or {}

        if not supplier_id:
            return Response({"error": "supplier_id is required"}, status=400)
        if not isinstance(rows, list) or not rows:
            return Response({"error": "rows must be a non-empty list"}, status=400)

        try:
            result = SourcingProductService().import_and_add(
                po=po,
                supplier_id=supplier_id,
                rows=rows,
                dim_mismatch_resolutions=dim_mismatch_resolutions,
            )
            return Response(result, status=200)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)

    @action(detail=True, methods=["post"], url_path="resolve-sourcing-conflicts")
    def resolve_sourcing_conflicts(self, request: Request, pk: Any = None) -> Response:
        from apps.purchasing.services.sourcing_product_service import SourcingProductService

        po = self.get_object()
        resolutions = request.data.get("resolutions")
        if not resolutions or not isinstance(resolutions, list):
            return Response(
                {"error": "resolutions must be a non-empty list"},
                status=400,
            )

        try:
            result = SourcingProductService().resolve_sourcing_conflicts(
                po=po, resolutions=resolutions
            )
            return Response(result, status=200)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)


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
        avg_sales_7d, avg_sales_14d, avg_sales_30d
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
            product_variant__company=request.user.profile.company,
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
            purchase_order__company=request.user.profile.company,
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
                ProductVariant.objects.filter(
                    is_active=True, company=request.user.profile.company
                ).values_list("id", flat=True)
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
        avg14 = svc.get_avg_sales_per_day(variant_ids=all_ids, days=14)
        avg14_map = {r["variant_id"]: r["avg_sales_per_day"] for r in avg14}

        # Variant metadata
        variants = ProductVariant.objects.filter(
            id__in=all_ids, company=request.user.profile.company
        ).select_related("product")

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
                    "avg_sales_14d": avg14_map.get(vid, 0.0),
                    "avg_sales_30d": avg30_map.get(vid, 0.0),
                }
            )

        results.sort(key=lambda r: r["stock_on_hand"])

        return Response({"results": results})


class SourcingPoolViewSet(viewsets.ViewSet):
    """
    /api/purchasing/sourcing-pool/
    """

    permission_classes = [IsStaffOrReadOnly]

    @action(detail=False, methods=["get"], url_path="template")
    def template(self, request: Request) -> Response:
        """GET /api/purchasing/sourcing-pool/template/ — returns .xlsx"""
        import io

        from django.http import HttpResponse

        from apps.purchasing.services.sourcing_service import SourcingService

        wb = SourcingService().build_template_workbook(company=request.user.profile.company)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = 'attachment; filename="sourcing_template.xlsx"'
        return resp

    @action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request: Request) -> Response:
        """
        POST /api/purchasing/sourcing-pool/preview/
        Body: multipart with field "file" (.xlsx).
        Returns { valid: [...], errors: [...] }. Does NOT commit.
        """
        from apps.purchasing.services.sourcing_service import SourcingService

        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"error": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        result = SourcingService().parse_excel_preview(
            file_bytes=uploaded.read(),
            company=request.user.profile.company,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get", "post", "delete"], url_path="color-abbreviations")
    def color_abbreviations(self, request: Request) -> Response:
        from apps.purchasing.models import ColorAbbreviation

        if request.method == "GET":
            items = ColorAbbreviation.objects.filter(company=request.user.profile.company).order_by(
                "color_name"
            )
            data = [
                {"id": str(i.id), "color_name": i.color_name, "abbreviation": i.abbreviation}
                for i in items
            ]
            return Response(data)

        if request.method == "DELETE":
            color_name = (request.data.get("color_name") or "").strip()
            if not color_name:
                return Response(
                    {"error": "color_name is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            deleted_count, _ = ColorAbbreviation.objects.filter(
                company=request.user.profile.company,
                color_name=color_name,
            ).delete()
            if deleted_count == 0:
                return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
            return Response(status=status.HTTP_204_NO_CONTENT)

        if request.method == "POST":
            color_name = (request.data.get("color_name") or "").strip()
            abbreviation = (request.data.get("abbreviation") or "").strip()
            if not color_name:
                return Response(
                    {"error": "color_name is required"}, status=status.HTTP_400_BAD_REQUEST
                )
            if not abbreviation:
                return Response(
                    {"error": "abbreviation is required"}, status=status.HTTP_400_BAD_REQUEST
                )
            obj, created = ColorAbbreviation.objects.get_or_create(
                company=request.user.profile.company,
                color_name=color_name,
                defaults={"abbreviation": abbreviation},
            )
            if not created:
                obj.abbreviation = abbreviation
                obj.save(update_fields=["abbreviation"])
            return Response(
                {"id": str(obj.id), "color_name": obj.color_name, "abbreviation": obj.abbreviation},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
