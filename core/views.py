from rest_framework import viewsets

from core.models import Company
from core.serializers import CompanySerializer


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.filter(is_active=True).all()
    serializer_class = CompanySerializer
