import logging
from decimal import Decimal
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
    SourcingPool,
    SourcingPoolItem,
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


class SourcingPoolItemPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


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

    @action(detail=True, methods=["post"], url_path="draft-lines")
    def add_draft_line(self, request: Request, pk: Any = None) -> Response:
        """POST /purchase-orders/{id}/draft-lines/ — add a sourcing item as a draft PO line."""
        po = self.get_object()
        sourcing_item_id = request.data.get("sourcing_item_id")
        ordered_qty = request.data.get("ordered_qty")
        unit_price_foreign = request.data.get("unit_price_foreign")

        if not sourcing_item_id:
            return Response(
                {"error": "sourcing_item_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if ordered_qty is None:
            return Response(
                {"error": "ordered_qty is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            ordered_qty_int = int(ordered_qty)
        except (TypeError, ValueError):
            return Response(
                {"error": "ordered_qty must be an integer"}, status=status.HTTP_400_BAD_REQUEST
            )

        price_decimal: Decimal | None = None
        if unit_price_foreign is not None:
            try:
                from decimal import Decimal as D

                price_decimal = D(str(unit_price_foreign))
            except Exception:
                return Response(
                    {"error": "unit_price_foreign must be a number"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            detail = PurchaseOrderService().add_draft_line(
                po=po,
                sourcing_item_id=str(sourcing_item_id),
                ordered_qty=ordered_qty_int,
                unit_price_foreign=price_decimal,
            )
            return Response({"detail_id": str(detail.id)}, status=status.HTTP_201_CREATED)
        except SourcingPoolItem.DoesNotExist:
            return Response({"error": "Sourcing item not found"}, status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"details/(?P<detail_id>[^/.]+)/finalize",
    )
    def finalize_draft_line(
        self, request: Request, pk: Any = None, detail_id: str = ""
    ) -> Response:
        """POST /purchase-orders/{id}/details/{detail_id}/finalize/ — create Product+Variant."""
        po = self.get_object()
        sku_suffix = request.data.get("sku_suffix", "").strip()
        category_id = request.data.get("category_id") or None
        product_name = request.data.get("product_name") or None

        if not sku_suffix:
            return Response({"error": "sku_suffix is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            detail = PurchaseOrderDetail.objects.get(id=detail_id, purchase_order=po)
        except PurchaseOrderDetail.DoesNotExist:
            return Response({"error": "Detail not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            detail = PurchaseOrderService().finalize_draft_line(
                detail=detail,
                sku_suffix=sku_suffix,
                category_id=category_id,
                product_name=product_name,
            )
            return Response(
                {"detail_id": str(detail.id), "variant_id": str(detail.product_variant.id)},  # type: ignore[union-attr]
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

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

    @action(detail=False, methods=["post"], url_path="import")
    def import_rows(self, request: Request) -> Response:
        """
        POST /api/purchasing/sourcing-pool/import/
        Body: { "supplier_id": "<ulid>", "rows": [...rows from preview valid list...] }
        Returns: { created: N, updated: N, pool_id: "<ulid>" }
        On ValueError from service: returns HTTP 400 with {"error": "<message>"}.
        """
        from apps.inventory.models import Supplier
        from apps.purchasing.services.sourcing_service import SourcingService

        supplier_id = request.data.get("supplier_id")
        rows = request.data.get("rows")

        if not supplier_id:
            return Response(
                {"error": "supplier_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not isinstance(rows, list) or not rows:
            return Response(
                {"error": "rows must be a non-empty list"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            supplier = Supplier.objects.get(id=supplier_id, company=request.user.profile.company)
        except Supplier.DoesNotExist:
            return Response({"error": "Supplier not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = SourcingService().import_rows(
                company=request.user.profile.company,
                supplier=supplier,
                rows=rows,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="items")
    def list_items(self, request: Request) -> Response:
        """
        GET /api/purchasing/sourcing-pool/items/?supplier_id=<ulid>&search=<str>
        search filters on product_name OR variant_name (case-insensitive).
        Returns { pool_id, items: [] } with HTTP 200 even when no pool exists yet (not 404).
        """
        from django.db.models import Q

        from apps.purchasing.serializers import SourcingPoolItemSerializer

        supplier_id = request.query_params.get("supplier_id")
        if not supplier_id:
            return Response(
                {"error": "supplier_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            pool = SourcingPool.objects.get(
                supplier_id=supplier_id, company=request.user.profile.company
            )
        except SourcingPool.DoesNotExist:
            return Response({"pool_id": None, "items": []}, status=status.HTTP_200_OK)

        qs = (
            SourcingPoolItem.objects.filter(pool=pool)
            .select_related("category")
            .order_by("product_name", "variant_name")
        )
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(Q(product_name__icontains=search) | Q(variant_name__icontains=search))

        paginator = SourcingPoolItemPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = SourcingPoolItemSerializer(page, many=True, context={"request": request})
        response = paginator.get_paginated_response(serializer.data)
        response.data["pool_id"] = str(pool.id)
        return response

    @action(detail=False, methods=["post"], url_path="download-images")
    def download_images(self, request: Request) -> Response:
        from apps.purchasing.services.sourcing_service import SourcingService

        pool_id = request.data.get("pool_id")
        include_failed = bool(request.data.get("include_failed", False))

        if not pool_id:
            return Response({"error": "pool_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            pool = SourcingPool.objects.get(id=pool_id, company=request.user.profile.company)
        except SourcingPool.DoesNotExist:
            return Response({"error": "Pool not found"}, status=status.HTTP_404_NOT_FOUND)

        result = SourcingService().download_pool_images(pool=pool, include_failed=include_failed)
        return Response(result, status=status.HTTP_200_OK)


class SourcingPoolItemImageView(APIView):
    """
    GET /api/purchasing/sourcing-pool/items/<item_id>/image/
    Proxies the R2 image for a SourcingPoolItem thumbnail.
    Returns 404 if: item not found, belongs to another company, or image_file is None (not yet downloaded).
    In Phase 1, image_file is always None (download happens in Phase 2), so this always returns 404.
    The view is wired now so Phase 2 only needs to populate image_file — no URL change needed.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, item_id: str) -> Response:
        import requests as http_requests
        from django.http import Http404, HttpResponse

        try:
            item = SourcingPoolItem.objects.get(id=item_id, company=request.user.profile.company)
        except SourcingPoolItem.DoesNotExist:
            raise Http404

        if not item.image_file:
            raise Http404

        try:
            resp = http_requests.get(item.image_file.url, timeout=10)
            resp.raise_for_status()
            return HttpResponse(
                resp.content,
                content_type=resp.headers.get("Content-Type", "image/jpeg"),
            )
        except Exception:
            logger.warning("Image proxy failed for item %s", item_id, exc_info=True)
            raise Http404
