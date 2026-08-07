from typing import Any

from rest_framework import serializers

from core.models import Company, UserProfile
from core.utils import round_money_to_int


class RoundedMoneyField(serializers.IntegerField):
    """Report-only money field: rounds a full-precision value to a whole-rupiah
    integer at the serialization boundary.

    Use this on every read-only report serializer field that represents money —
    the shared, reusable mechanism behind the report side of the money
    serialization contract. Never use it on a CRUD (Model)Serializer field: those
    must keep DRF's default DecimalField string output, since a read-modify-write
    must not destroy precision.
    """

    def to_representation(self, value: Any) -> int:
        return round_money_to_int(value)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ["company_id", "role"]
