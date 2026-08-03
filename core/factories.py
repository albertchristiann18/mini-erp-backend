import factory
from django.contrib.auth.models import User

from apps.catalog.models import Category
from apps.inventory.models import Warehouse
from core.models import Company, UserProfile


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Company

    name = "Test Company"


class WarehouseFactory(factory.django.DjangoModelFactory):
    """Factory for creating test Warehouse instances"""

    class Meta:
        model = Warehouse

    name = "Test Warehouse"
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


class CategoryFactory(factory.django.DjangoModelFactory):
    """Factory for creating test Category instances"""

    class Meta:
        model = Category

    name = "Test Category"
    category_code = "TEST"
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user_{n:04d}")  # type: ignore[no-untyped-call]
    password = "x"
    is_staff = False


class UserProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserProfile

    user = factory.SubFactory(UserFactory)  # type: ignore[no-untyped-call]
    company = factory.SubFactory(CompanyFactory)  # type: ignore[no-untyped-call]
    role = "viewer"
