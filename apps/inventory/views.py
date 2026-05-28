from typing import Any, Type

from django.db import models, transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.inventory.constants.categories import MASTER_CATEGORY
from apps.inventory.models import Category, Product, ProductPhoto, StockMovement, Warehouse
from apps.inventory.serializers import (
    CategorySerializer,
    ProductCreateSerializer,
    ProductPhotoSerializer,
    ProductSerializer,
    ProductVariantStockSerializer,
    StockMovementSerializer,
    WarehouseSerializer,
)
from apps.inventory.services import product_service
from apps.inventory.services.bulk_inventory_service import BulkInventoryService
from core.permissions import IsStaffOrReadOnly


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.filter(is_active=True).all()
    serializer_class = CategorySerializer
    permission_classes = [IsStaffOrReadOnly]


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.none()
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet[Product]:
        if not self.request.user.is_authenticated:
            return Product.objects.none()
        qs = Product.objects.filter(is_active=True, company=self.request.user.profile.company)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(models.Q(name__icontains=search) | models.Q(sku_code__icontains=search))
        return qs

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action == "create":
            return ProductCreateSerializer
        return ProductSerializer

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from django.db import transaction

        from apps.inventory.services.product_service import ProductService

        response = super().update(request, *args, **kwargs)
        instance = self.get_object()
        transaction.on_commit(
            lambda: ProductService()._trigger_shopee_product_update(str(instance.id))
        )
        return response

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=["patch"], url_path="update_prices")
    def update_prices(self, request: Request, pk: str | None = None) -> Response:
        from apps.inventory.models import ProductVariantMarketplace
        from apps.inventory.services.product_service import ProductService

        product = self.get_object()
        price_updates = request.data
        if not isinstance(price_updates, list):
            return Response({"error": "Expected JSON array"}, status=status.HTTP_400_BAD_REQUEST)

        updated_listing_ids: list[str] = []
        errors: list[str] = []

        for item in price_updates:
            variant_id = item.get("variant_id")
            marketplace_id = item.get("marketplace_id")
            selling_price = item.get("selling_price")
            discounted_price = item.get("discounted_price")

            if not variant_id or not marketplace_id or selling_price is None:
                errors.append(f"Missing required fields in: {item}")
                continue

            update_fields: dict = {"selling_price": selling_price}
            if discounted_price is not None:
                update_fields["discounted_price"] = discounted_price

            qs = ProductVariantMarketplace.objects.filter(
                product_variant_id=variant_id,
                marketplace_id=marketplace_id,
                product_variant__product=product,
                product_variant__company=product.company,
            )
            updated = qs.update(**update_fields)
            if updated:
                listing = qs.first()
                if listing:
                    updated_listing_ids.append(str(listing.id))
            else:
                errors.append(
                    f"No listing found for variant_id={variant_id} marketplace_id={marketplace_id}"
                )

        if updated_listing_ids:
            _ids = updated_listing_ids
            _company_id = str(product.company_id)
            transaction.on_commit(
                lambda: ProductService()._trigger_shopee_price_update(_ids, _company_id)
            )

        return Response(
            {
                "updated": len(updated_listing_ids),
                "errors": errors,
            },
            status=status.HTTP_200_OK,
        )

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        is_many = isinstance(request.data, list)
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        services = product_service.ProductService()
        services.create_product_with_variants(validated_data)

        return Response(status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="photos",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_photo(self, request: Request, pk: str | None = None) -> Response:
        product = self.get_object()
        if product.photos.count() >= 9:
            return Response({"error": "Maximum 9 photos allowed"}, status=400)
        image = request.FILES.get("image")
        if not image:
            return Response({"error": "No image provided"}, status=400)
        order = product.photos.count()
        is_primary = order == 0
        photo = ProductPhoto.objects.create(
            product=product,
            company=product.company,
            image=image,
            order=order,
            is_primary=is_primary,
        )
        return Response(ProductPhotoSerializer(photo).data, status=201)

    @action(detail=True, methods=["delete"], url_path=r"photos/(?P<photo_id>[^/.]+)")
    def delete_photo(
        self, request: Request, pk: str | None = None, photo_id: str | None = None
    ) -> Response:
        photo = get_object_or_404(ProductPhoto, id=photo_id, product_id=pk)
        photo.delete()
        for i, p in enumerate(ProductPhoto.objects.filter(product_id=pk).order_by("order")):
            p.order = i
            p.is_primary = i == 0
            p.save()
        return Response(status=204)

    @action(detail=True, methods=["patch"], url_path=r"photos/(?P<photo_id>[^/.]+)/reorder")
    def reorder_photos(
        self, request: Request, pk: str | None = None, photo_id: str | None = None
    ) -> Response:
        photo_ids = request.data.get("photo_ids", [])
        for i, pid in enumerate(photo_ids):
            ProductPhoto.objects.filter(id=pid, product_id=pk).update(order=i, is_primary=(i == 0))
        photos = ProductPhoto.objects.filter(product_id=pk).order_by("order")
        return Response(ProductPhotoSerializer(photos, many=True).data)

    @action(detail=False, methods=["post"], url_path="bulk_create")
    def bulk_create_products(self, request: Request) -> Response:
        if not isinstance(request.data, list):
            return Response({"error": "Expected JSON array"}, status=400)
        serializer = ProductCreateSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        services = product_service.ProductService()
        services.create_product_with_variants(serializer.validated_data)
        return Response({"created": len(request.data), "errors": []}, status=201)


class ProductVariantStockViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Returns variants with their stock per warehouse.
    Query params:
      - warehouse: warehouse ID (optional) — if provided, returns physical_qty for that warehouse
      - search: filter by variant name, sku_variant_code, or parent product name
    """

    permission_classes = [IsStaffOrReadOnly]
    serializer_class = ProductVariantStockSerializer

    def get_queryset(self) -> QuerySet:
        from apps.inventory.models import ProductVariant

        if not self.request.user.is_authenticated:
            return ProductVariant.objects.none()
        qs = ProductVariant.objects.filter(
            is_active=True, company=self.request.user.profile.company
        ).select_related("product", "product__category")
        search = self.request.query_params.get("search")
        if search:
            from django.db import models as db_models

            qs = qs.filter(
                db_models.Q(name__icontains=search)
                | db_models.Q(sku_variant_code__icontains=search)
                | db_models.Q(product__name__icontains=search)
            )
        return qs.order_by("product__name", "name")


class MasterCategoryViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]

    def list(self, request: Request) -> Response:
        return Response(MASTER_CATEGORY)


class InventoryBulkViewSet(viewsets.ViewSet):
    permission_classes = [IsStaffOrReadOnly]

    @action(detail=False, methods=["post"], url_path="bulk_update")
    def bulk_update(self, request: Request) -> Response:
        updates = request.data
        if not isinstance(updates, list):
            return Response({"error": "Expected JSON array"}, status=400)
        result = BulkInventoryService.bulk_update(updates)
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

        result = BulkInventoryService.bulk_update(
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
        return Warehouse.objects.filter(
            is_active=True, company=self.request.user.profile.company
        )
