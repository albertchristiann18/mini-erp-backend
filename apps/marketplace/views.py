from typing import Any, Type

from django.db.models import QuerySet
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from apps.marketplace.models import (
    BusinessEntity,
    CompanyMarketplace,
    Marketplace,
    MarketplaceConnection,
    ProductBusinessEntity,
)
from apps.marketplace.serializers import (
    BusinessEntitySerializer,
    BusinessEntityWriteSerializer,
    CompanyMarketplaceSerializer,
    CompanyMarketplaceWriteSerializer,
    MarketplaceConnectionSerializer,
    MarketplaceSerializer,
    ProductBusinessEntitySerializer,
)
from apps.marketplace.services.business_entity_service import BusinessEntityService


class MarketplaceViewSet(viewsets.ModelViewSet):
    queryset = Marketplace.objects.filter(is_active=True).all()
    serializer_class = MarketplaceSerializer


class MarketplaceConnectionViewSet(viewsets.ModelViewSet):
    serializer_class = MarketplaceConnectionSerializer

    def get_queryset(self) -> QuerySet[MarketplaceConnection]:
        user = self.request.user
        if user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile:
                return MarketplaceConnection.objects.filter(company=profile.company)
        return MarketplaceConnection.objects.all()

    def perform_create(self, serializer: MarketplaceConnectionSerializer) -> None:
        user = self.request.user
        profile = getattr(user, "profile", None) if user.is_authenticated else None
        company = profile.company if profile else None
        serializer.save(company=company)

    @action(detail=True, methods=["post"])
    def toggle_active(self, request: Request, pk: str | None = None) -> Response:
        conn = self.get_object()
        conn.is_active = not conn.is_active
        conn.save()
        return Response(MarketplaceConnectionSerializer(conn).data)


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
        """GET /product-business-entities/?product_id=<id>"""
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
        """POST /product-business-entities/
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
        """DELETE /product-business-entities/{id}/"""
        try:
            BusinessEntityService().detach_product(
                product_business_entity_id=pk or "",
                company_id=str(request.user.profile.company_id),
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
