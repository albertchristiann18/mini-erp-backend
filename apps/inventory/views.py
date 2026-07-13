from typing import cast

from django.db.models import Prefetch, QuerySet
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product, ProductPhoto, ProductVariant
from apps.inventory.models import (
    StockMovement,
    Warehouse,
)
from apps.inventory.serializers import (
    StockMovementSerializer,
    WarehouseSerializer,
)
from apps.inventory.services.inventory_service import InventoryService
from core.permissions import IsStaffOrReadOnly


class InventoryBulkViewSet(viewsets.ViewSet):
    permission_classes = [IsStaffOrReadOnly]

    @action(detail=False, methods=["post"], url_path="bulk_update")
    def bulk_update(self, request: Request) -> Response:
        updates = request.data
        if not isinstance(updates, list):
            return Response({"error": "Expected JSON array"}, status=400)
        result = InventoryService().adjust_stock_batch(updates)
        return Response(result, status=200)

    @action(detail=False, methods=["post"], url_path="adjust")
    def adjust(self, request: Request) -> Response:
        """
        Single variant stock adjustment.
        Body: { variant_id, warehouse_id, type: 'add'|'min'|'set', qty: int }
        """
        variant_id = request.data.get("variant_id")
        warehouse_id = request.data.get("warehouse_id")
        adj_type = request.data.get("type")
        qty = request.data.get("qty")

        if not all([variant_id, warehouse_id, adj_type, qty is not None]):
            return Response(
                {"error": "variant_id, warehouse_id, type, qty are required"}, status=400
            )
        if adj_type not in ("add", "min", "set"):
            return Response({"error": "type must be add, min, or set"}, status=400)

        result = InventoryService().adjust_stock_batch(
            [
                {
                    "variant_id": variant_id,
                    "warehouse_id": warehouse_id,
                    "qty": qty,
                    "type": adj_type,
                }
            ]
        )
        return Response(result, status=200)

    @action(
        detail=False,
        methods=["post"],
        url_path="marketplace_reconcile",
        parser_classes=[MultiPartParser, FormParser],
    )
    def marketplace_reconcile(self, request: Request) -> Response:
        file_obj = request.FILES.get("file")
        marketplace_id = request.data.get("marketplace_id", "")
        warehouse_id = request.data.get("warehouse_id")
        dry_run_raw = request.data.get("dry_run", "false")
        dry_run = str(dry_run_raw).lower() in ("true", "1", "yes")

        if not file_obj:
            return Response({"error": "file is required"}, status=400)
        if not warehouse_id:
            return Response({"error": "warehouse_id is required"}, status=400)

        from apps.marketplace.models import CompanyMarketplace

        marketplace_name = marketplace_id
        if marketplace_id:
            cm = CompanyMarketplace.objects.filter(
                id=marketplace_id,
                company=request.user.profile.company,
            ).first()
            if cm:
                marketplace_name = cm.name

        rows = InventoryService.parse_marketplace_xlsx(file_obj)
        if not rows:
            return Response(
                {
                    "error": "Could not parse file. Ensure it has SKU and stock columns in the first 5 rows."
                },
                status=400,
            )

        result = InventoryService().reconcile_marketplace_stock(
            rows=rows,
            warehouse_id=warehouse_id,
            company_id=str(request.user.profile.company_id),
            marketplace_name=marketplace_name,
            dry_run=dry_run,
        )
        return Response(result, status=200)


class AvgSalesView(APIView):
    """
    GET /api/inventory/avg-sales/
    Query params:
      - days: int (default 30, must be 7 or 30)
      - variant_ids: comma-separated string of variant IDs
                     e.g. ?variant_ids=01ABC,01DEF
    Returns AVG sales/day per variant over the specified window.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        days_param = request.query_params.get("days", "30")
        try:
            days = int(days_param)
        except ValueError:
            return Response(
                {"error": "days must be an integer (7 or 30)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if days not in [7, 30]:
            return Response(
                {"error": "days must be 7 or 30"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        variant_ids_param = request.query_params.get("variant_ids", "")
        if not variant_ids_param:
            return Response(
                {"error": "variant_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        variant_ids = [v.strip() for v in variant_ids_param.split(",") if v.strip()]
        if not variant_ids:
            return Response(
                {"error": "variant_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.inventory.services.inventory_service import InventoryService

        service = InventoryService()
        results = service.get_avg_sales_per_day(variant_ids=variant_ids, days=days)

        from datetime import timedelta

        from django.utils import timezone

        date_from = (timezone.now().date() - timedelta(days=days)).isoformat()

        return Response(
            {
                "days": days,
                "date_from": date_from,
                "results": results,
            }
        )


class InventorySummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        company = request.user.profile.company

        warehouses = list(
            Warehouse.objects.filter(is_active=True, company=company).order_by("name")
        )

        products = (
            Product.objects.filter(is_active=True, company=company)
            .order_by("sku_code")
            .prefetch_related(
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .order_by("sku_variant_code")
                    .prefetch_related("warehouse_stocks"),
                    to_attr="active_variants",
                ),
                Prefetch(
                    "photos",
                    queryset=ProductPhoto.objects.filter(is_primary=True),
                    to_attr="primary_photos",
                ),
            )
        )

        total_cogs_stock = 0
        total_selling_price = 0
        total_variants = 0

        product_list = []
        for product in products:
            variant_list = []
            for _v in product.active_variants:
                v = cast(ProductVariant, _v)
                pvw_list = list(v.warehouse_stocks.all())
                total_qty = sum(pvw.physical_qty for pvw in pvw_list)
                warehouse_stocks = {str(pvw.warehouse_id): pvw.physical_qty for pvw in pvw_list}  # type: ignore[attr-defined]
                total_cogs_stock += v.current_cogs * total_qty
                total_selling_price += v.base_price * total_qty
                total_variants += 1
                variant_list.append(
                    {
                        "variant_id": str(v.id),
                        "sku_variant_code": v.sku_variant_code,
                        "variant_name": v.name,
                        "variant_values": v.variant_values,
                        "total_qty": total_qty,
                        "warehouse_stocks": warehouse_stocks,
                        "current_cogs": v.current_cogs,
                        "base_price": v.base_price,
                    }
                )

            primary_photo = cast(
                ProductPhoto | None, product.primary_photos[0] if product.primary_photos else None
            )
            product_list.append(
                {
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "sku_code": product.sku_code,
                    "photo_url": (
                        request.build_absolute_uri(primary_photo.image.url)
                        if primary_photo
                        else None
                    ),
                    "variants": variant_list,
                }
            )

        return Response(
            {
                "warehouses": [{"id": str(w.id), "name": w.name} for w in warehouses],
                "products": product_list,
                "summary": {
                    "total_cogs_stock": total_cogs_stock,
                    "total_selling_price": total_selling_price,
                    "total_products": len(product_list),
                    "total_variants": total_variants,
                },
            }
        )


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self) -> QuerySet:
        qs = (
            StockMovement.objects.filter(company=self.request.user.profile.company).select_related(
                "product_variant", "warehouse"
            )
            if self.request.user.is_authenticated
            else StockMovement.objects.none()
        )

        warehouse_id = self.request.query_params.get("warehouse")
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)

        product_variant_id = self.request.query_params.get("product_variant")
        if product_variant_id:
            qs = qs.filter(product_variant_id=product_variant_id)

        movement_type = self.request.query_params.get("movement_type")
        if movement_type:
            try:
                short_code = StockMovement.MovementType[movement_type].value
                qs = qs.filter(movement_type=short_code)
            except KeyError:
                qs = qs.filter(movement_type=movement_type)

        cdate_after = self.request.query_params.get("cdate_after")
        if cdate_after:
            qs = qs.filter(cdate__date__gte=cdate_after)

        cdate_before = self.request.query_params.get("cdate_before")
        if cdate_before:
            qs = qs.filter(cdate__date__lte=cdate_before)

        return qs


class WarehouseViewSet(viewsets.ModelViewSet):
    queryset = Warehouse.objects.none()
    serializer_class = WarehouseSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet:
        if not self.request.user.is_authenticated:
            return Warehouse.objects.none()
        return Warehouse.objects.filter(is_active=True, company=self.request.user.profile.company)
