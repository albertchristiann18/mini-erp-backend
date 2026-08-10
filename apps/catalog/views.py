import mimetypes
from typing import Any, Type

from django.db import models, transaction
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from apps.catalog.constants.categories import MASTER_CATEGORY
from apps.catalog.models import (
    Category,
    Product,
    ProductPhoto,
    ProductVariant,
    ProductVariantMarketplace,
)
from apps.catalog.serializers import (
    CategorySerializer,
    ProductCreateSerializer,
    ProductPhotoSerializer,
    ProductSerializer,
    ProductVariantStockSerializer,
    UpdatePriceItemSerializer,
    UpdateVariantPriceSerializer,
)
from apps.catalog.services import product_service
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
        from apps.catalog.services.product_service import ProductService

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
        from apps.catalog.services.product_service import ProductService

        product = self.get_object()
        price_updates = request.data
        if not isinstance(price_updates, list):
            return Response({"error": "Expected JSON array"}, status=status.HTTP_400_BAD_REQUEST)

        updated_listing_ids: list[str] = []
        errors: list[str] = []

        for item in price_updates:
            item_serializer = UpdatePriceItemSerializer(data=item)
            if not item_serializer.is_valid():
                errors.append(f"Missing or invalid fields in: {item}")
                continue

            validated = item_serializer.validated_data
            variant_id = validated["variant_id"]
            marketplace_id = validated["marketplace_id"]

            update_fields: dict = {"selling_price": validated["selling_price"]}
            if "discounted_price" in validated and validated["discounted_price"] is not None:
                update_fields["discounted_price"] = validated["discounted_price"]

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

        price_serializer = UpdateVariantPriceSerializer(data=request.data)
        if not price_serializer.is_valid():
            return Response(
                {"error": "base_price is required and must be a non-negative number"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        base_price = price_serializer.validated_data["base_price"]

        from apps.catalog.services.product_service import ProductService

        result = ProductService().update_variant_base_price(str(variant.id), base_price)
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="save_variants")
    def save_variants(self, request: Request, pk: str | None = None) -> Response:
        product = self.get_object()
        from apps.catalog.serializers import SaveVariantsSerializer
        from apps.catalog.services.product_service import ProductService

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

            from apps.catalog.services.product_service import ProductService

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

        from apps.catalog.services.product_service import ProductService

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
