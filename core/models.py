from django.contrib.auth import get_user_model
from django.db import models
from django_ulid.models import ULIDField

from core.utils import generate_ulid

User = get_user_model()


class TimeStampedModel(models.Model):
    class Meta(object):
        abstract = True

    cdate = models.DateTimeField(auto_now_add=True)
    udate = models.DateTimeField(auto_now=True)


class Company(TimeStampedModel):
    id = ULIDField(primary_key=True, default=generate_ulid, editable=False, db_column="company_id")
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    tax_id = models.CharField(max_length=100, unique=True, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.name}"


class DefaultModel(TimeStampedModel):
    class Meta(object):
        abstract = True

    company = models.ForeignKey(Company, on_delete=models.CASCADE, db_column="company_id")


class UserProfile(TimeStampedModel):
    ROLE_CHOICES = [
        ("admin", "Admin"),
        ("cs", "Customer Service"),
        ("warehouse", "Warehouse"),
        ("finance", "Finance"),
        ("viewer", "Viewer"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="members")
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default="viewer")

    class Meta:
        db_table = "user_profile"

    def __str__(self) -> str:
        return f"{self.user.username} @ {self.company.name} ({self.role})"
