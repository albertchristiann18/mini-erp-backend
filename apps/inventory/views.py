import mimetypes
from typing import Any, Type, cast

from django.db import models, transaction
from django.db.models import Prefetch, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.inventory.constants.categories import MASTER_CATEGORY
from apps.inventory.models import (
    BusinessEntity,
    Category,
    CompanyMarketplace,
    Product,
    ProductBusinessEntity,
    ProductPhoto,
    ProductSupplier,
    ProductVariant,
    StockMovement,
    Supplier,
    Warehouse,
)
from apps.inventory.serializers import (
    BusinessEntitySerializer,
    BusinessEntityWriteSerializer,
    CategorySerializer,
    CompanyMarketplaceSerializer,
    CompanyMarketplaceWriteSerializer,
    ProductBusinessEntitySerializer,
    ProductCreateSerializer,
    ProductPhotoSerializer,
    ProductSerializer,
    ProductSupplierSerializer,
    ProductVariantStockSerializer,
    StockMovementSerializer,
    SupplierSerializer,
    WarehouseSerializer,
)
from apps.inventory.services import product_service
from apps.inventory.services.business_entity_service import BusinessEntityService
from apps.inventory.services.inventory_service import InventoryService
from core.permissions import IsStaffOrReadOnly


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [IsStaffOrReadOnly]

    def perform_create(self, serializer: Serializer) -> None:
        serializer.save(company=self.request.user.profile.company)

    def get_queryset(self) -> QuerySet["Category"]:
        qs = Category.objects.filter(
            company=self.request.user.profile.company,
            is_active=True,
        )
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search)
        return qs.order_by("name")

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        linked_products = list(Product.objects.filter(category=instance).values("name", "sku_code"))
        if linked_products:
            return Response(
                {
                    "error": "Cannot delete category — products are linked",
                    "products": linked_products,
                },
                status=status.HTTP_409_CONFLICT,
            )
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.none()
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self) -> QuerySet[Product]:
        if not self.request.user.is_authenticated:
            return Product.objects.none()
        qs = Product.objects.filter(company=self.request.user.profile.company)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(models.Q(name__icontains=search) | models.Q(sku_code__icontains=search))
        category_id = self.request.query_params.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)
        ordering = self.request.query_params.get("ordering")
        ALLOWED_ORDERINGS = {"name", "-name", "sku_code", "-sku_code"}
        if ordering and ordering in ALLOWED_ORDERINGS:
            qs = qs.order_by(ordering)
        return qs.prefetch_related("dimension_images", "photos")

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action == "create":
            return ProductCreateSerializer
        return ProductSerializer

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from apps.inventory.services.product_service import ProductService

        current = self.get_object()
        old_dim1_key = current.dim1_key
        old_dim2_key = current.dim2_key
        _product_id = str(current.id)

        response = super().update(request, *args, **kwargs)

        transaction.on_commit(lambda: ProductService()._trigger_shopee_product_update(_product_id))
        transaction.on_commit(
            lambda: ProductService().cleanup_orphan_dimension_images(
                _product_id, old_dim1_key, old_dim2_key
            )
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

    @action(detail=True, methods=["patch"], url_path=r"update_variant_price/(?P<variant_id>[^/.]+)")
    def update_variant_price(
        self, request: Request, pk: str | None = None, variant_id: str | None = None
    ) -> Response:
        product = self.get_object()
        try:
            variant = ProductVariant.objects.get(
                id=variant_id, product=product, company=product.company
            )
        except ProductVariant.DoesNotExist:
            return Response({"error": "Variant not found"}, status=status.HTTP_404_NOT_FOUND)

        base_price = request.data.get("base_price")
        if base_price is None or not isinstance(base_price, int) or base_price < 0:
            return Response(
                {"error": "base_price is required and must be a non-negative integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.inventory.services.product_service import ProductService

        result = ProductService().update_variant_base_price(str(variant.id), base_price)
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="save_variants")
    def save_variants(self, request: Request, pk: str | None = None) -> Response:
        product = self.get_object()
        from apps.inventory.serializers import SaveVariantsSerializer
        from apps.inventory.services.product_service import ProductService

        serializer = SaveVariantsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = ProductService().save_variants(
            product_id=str(product.id),
            company_id=str(product.company_id),
            variant_options=serializer.validated_data["variant_options"],
            variants=list(serializer.validated_data["variants"]),
        )
        return Response(result, status=status.HTTP_200_OK)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        is_many = isinstance(request.data, list)
        serializer = self.get_serializer(data=request.data, many=is_many)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        services = product_service.ProductService()
        created = services.create_product_with_variants(validated_data)

        if is_many:
            return Response(created, status=status.HTTP_201_CREATED)
        else:
            return Response(created[0] if created else {}, status=status.HTTP_201_CREATED)

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

    @action(
        detail=True,
        methods=["post", "delete"],
        url_path=r"variants/(?P<variant_id>[^/.]+)/photo",
        parser_classes=[MultiPartParser, FormParser],
    )
    def manage_variant_photo(
        self, request: Request, pk: str | None = None, variant_id: str | None = None
    ) -> Response:
        product = self.get_object()
        variant = get_object_or_404(
            ProductVariant, id=variant_id, product=product, company=product.company
        )
        if request.method == "DELETE":
            if variant.photo:
                variant.photo.delete(save=False)
                variant.save(update_fields=["photo", "udate"])
            return Response(status=204)
        image = request.FILES.get("image")
        if not image:
            return Response({"error": "No image provided"}, status=400)
        if variant.photo:
            variant.photo.delete(save=False)
        variant.photo = image
        variant.save(update_fields=["photo", "udate"])
        photo_url = variant.photo.url if variant.photo else None
        return Response({"photo_url": photo_url}, status=200)

    @action(
        detail=True,
        methods=["post", "delete"],
        url_path="dimension-image",
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def manage_dimension_image(self, request: Request, pk: str | None = None) -> Response:
        product = self.get_object()
        if request.method == "DELETE":
            dim_key = (request.data.get("dim_key") or "").strip()
            dim_value = (request.data.get("dim_value") or "").strip()

            if not dim_key:
                return Response(
                    {"error": "dim_key is required"}, status=status.HTTP_400_BAD_REQUEST
                )
            if not dim_value:
                return Response(
                    {"error": "dim_value is required"}, status=status.HTTP_400_BAD_REQUEST
                )

            from apps.inventory.services.product_service import ProductService

            try:
                ProductService().delete_dimension_image(product, dim_key, dim_value)
            except ValueError:
                return Response(
                    {"error": "Dimension image not found"}, status=status.HTTP_404_NOT_FOUND
                )
            return Response(status=status.HTTP_204_NO_CONTENT)

        # POST
        dim_key = request.data.get("dim_key", "").strip()
        dim_value = request.data.get("dim_value", "").strip()
        photo_file = request.FILES.get("photo")

        if not dim_key:
            return Response({"error": "dim_key is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not dim_value:
            return Response({"error": "dim_value is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not photo_file:
            return Response({"error": "photo is required"}, status=status.HTTP_400_BAD_REQUEST)

        from apps.inventory.services.product_service import ProductService

        try:
            dim_img = ProductService().upsert_dimension_image(
                product, dim_key, dim_value, photo_file
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "id": str(dim_img.id),
                "dim_key": dim_img.dim_key,
                "dim_value": dim_img.dim_value,
                "photo_url": dim_img.photo.url if dim_img.photo else None,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"], url_path="photo-proxy")
    def photo_proxy(self, request: Request, pk: str | None = None) -> Response:
        product = self.get_object()
        dim_key = request.query_params.get("dim_key", "").strip()
        dim_value = request.query_params.get("dim_value", "").strip()

        photo_file = None

        if dim_key and dim_value:
            for di in product.dimension_images.all():
                if di.dim_key == dim_key and di.dim_value == dim_value:
                    if di.photo:
                        photo_file = di.photo
                    break

        if photo_file is None:
            gallery_list = sorted(product.photos.all(), key=lambda p: p.order)
            if gallery_list and gallery_list[0].image:
                photo_file = gallery_list[0].image
            elif product.product_photo:
                photo_file = product.product_photo

        if photo_file is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        content_type = mimetypes.guess_type(photo_file.name)[0] or "image/jpeg"
        photo_file.open("rb")
        data: bytes = photo_file.read()
        photo_file.close()
        http_response = HttpResponse(data, content_type=content_type)
        http_response["Cache-Control"] = "private, max-age=3600"
        return http_response  # type: ignore[return-value]


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
        qs = (
            ProductVariant.objects.filter(is_active=True, company=self.request.user.profile.company)
            .select_related("product", "product__category")
            .prefetch_related("product__product_suppliers", "product__photos")
        )
        search = self.request.query_params.get("search")
        if search:
            from django.db import models as db_models

            qs = qs.filter(
                db_models.Q(name__icontains=search)
                | db_models.Q(sku_variant_code__icontains=search)
                | db_models.Q(product__name__icontains=search)
            )
        supplier_id = self.request.query_params.get("supplier_id")
        if supplier_id:
            qs = qs.filter(product__product_suppliers__supplier__id=supplier_id).distinct()
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

        from apps.inventory.models import CompanyMarketplace

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


class SupplierViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer: Any) -> None:
        serializer.save(company=self.request.user.profile.company)

    def get_queryset(self) -> QuerySet["Supplier"]:
        qs = Supplier.objects.filter(company=self.request.user.profile.company)
        if self.request.query_params.get("active_only"):
            qs = qs.filter(is_active=True)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search)
        return qs.order_by("name")


class ProductSupplierViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSupplierSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self) -> QuerySet["ProductSupplier"]:
        qs = ProductSupplier.objects.filter(
            company=self.request.user.profile.company
        ).select_related("supplier", "product")
        product_id = self.request.query_params.get("product_id")
        supplier_id = self.request.query_params.get("supplier_id")
        if product_id:
            qs = qs.filter(product__id=product_id)
        if supplier_id:
            qs = qs.filter(supplier__id=supplier_id)
        return qs


class CompanyMarketplaceViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request: Request) -> Response:
        """GET — returns company's channels, auto-seeds Shopee+TikTok on first call."""
        company_id = str(request.user.profile.company_id)
        marketplaces = BusinessEntityService().get_or_seed_company_marketplaces(company_id)
        search = request.query_params.get("search")
        if search:
            marketplaces = [m for m in marketplaces if search.lower() in m.name.lower()]
        serializer = CompanyMarketplaceSerializer(marketplaces, many=True)
        return Response({"count": len(serializer.data), "results": serializer.data})

    def create(self, request: Request) -> Response:
        """POST — create a new company marketplace."""
        serializer = CompanyMarketplaceWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        company = request.user.profile.company
        name = serializer.validated_data["name"].strip()
        is_active = serializer.validated_data.get("is_active", True)
        if CompanyMarketplace.objects.filter(company=company, name=name).exists():
            return Response(
                {"error": f"Marketplace '{name}' already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj = CompanyMarketplace.objects.create(company=company, name=name, is_active=is_active)
        return Response(CompanyMarketplaceSerializer(obj).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, pk: str | None = None) -> Response:
        """PATCH — update name or is_active."""
        try:
            obj = CompanyMarketplace.objects.get(id=pk, company=request.user.profile.company)
        except CompanyMarketplace.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = CompanyMarketplaceWriteSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        for attr, value in serializer.validated_data.items():
            setattr(obj, attr, value)
        obj.save()
        return Response(CompanyMarketplaceSerializer(obj).data)

    def destroy(self, request: Request, pk: str | None = None) -> Response:
        """DELETE — only allowed if no BusinessEntities use this marketplace."""
        try:
            obj = CompanyMarketplace.objects.get(id=pk, company=request.user.profile.company)
        except CompanyMarketplace.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if obj.business_entities.exists():
            return Response(
                {"error": "Cannot delete — business entities are using this marketplace"},
                status=status.HTTP_409_CONFLICT,
            )
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BusinessEntityViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self) -> Type[Serializer]:
        if self.action in ("create", "update", "partial_update"):
            return BusinessEntityWriteSerializer
        return BusinessEntitySerializer

    def get_queryset(self) -> QuerySet[BusinessEntity]:
        if not self.request.user.is_authenticated:
            return BusinessEntity.objects.none()
        qs = (
            BusinessEntity.objects.filter(company=self.request.user.profile.company)
            .select_related("marketplace")
            .order_by("name")
        )
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    def perform_create(self, serializer: BusinessEntityWriteSerializer) -> None:
        company = self.request.user.profile.company
        marketplace_id = serializer.validated_data.pop("marketplace_id")
        try:
            marketplace = CompanyMarketplace.objects.get(id=marketplace_id, company=company)
        except CompanyMarketplace.DoesNotExist:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(
                {"marketplace_id": "Marketplace not found or does not belong to your company"}
            )
        serializer.save(company=company, marketplace=marketplace)

    def perform_update(self, serializer: BusinessEntityWriteSerializer) -> None:
        company = self.request.user.profile.company
        marketplace_id = serializer.validated_data.pop("marketplace_id", None)
        if marketplace_id:
            try:
                marketplace = CompanyMarketplace.objects.get(id=marketplace_id, company=company)
            except CompanyMarketplace.DoesNotExist:
                from rest_framework.exceptions import ValidationError

                raise ValidationError({"marketplace_id": "Marketplace not found"})
            serializer.save(marketplace=marketplace)
        else:
            serializer.save()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from django.db import IntegrityError

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except IntegrityError:
            return Response(
                {"error": "Business entity with this name already exists for this company"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        read_serializer = BusinessEntitySerializer(serializer.instance)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from django.db import IntegrityError

        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_update(serializer)
        except IntegrityError:
            return Response(
                {"error": "Business entity with this name already exists for this company"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        read_serializer = BusinessEntitySerializer(serializer.instance)
        return Response(read_serializer.data)


class ProductBusinessEntityViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request: Request) -> Response:
        """GET /api/inventory/product-business-entities/?product_id=<id>"""
        product_id = request.query_params.get("product_id")
        qs = ProductBusinessEntity.objects.filter(
            company=request.user.profile.company
        ).select_related("product", "business_entity", "business_entity__marketplace")
        if product_id:
            qs = qs.filter(product_id=product_id)
        serializer = ProductBusinessEntitySerializer(qs, many=True)
        return Response(
            {
                "count": len(serializer.data),
                "next": None,
                "previous": None,
                "results": serializer.data,
            }
        )

    def create(self, request: Request) -> Response:
        """POST /api/inventory/product-business-entities/
        Body: { product_id: str, business_entity_id: str }
        """
        product_id = request.data.get("product_id")
        business_entity_id = request.data.get("business_entity_id")
        if not product_id or not business_entity_id:
            return Response(
                {"error": "product_id and business_entity_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = BusinessEntityService().attach_product(
                product_id=product_id,
                business_entity_id=business_entity_id,
                company_id=str(request.user.profile.company_id),
            )
        except (ValueError, Exception) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, pk: str | None = None) -> Response:
        """DELETE /api/inventory/product-business-entities/{id}/"""
        try:
            BusinessEntityService().detach_product(
                product_business_entity_id=pk or "",
                company_id=str(request.user.profile.company_id),
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
