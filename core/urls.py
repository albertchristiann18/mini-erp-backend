"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core import views as core_views


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def me_view(request: Request) -> Response:
    user = request.user
    profile = getattr(user, "profile", None)

    if request.method == "PATCH":
        data = request.data
        errors: dict[str, list[str]] = {}

        new_username = data.get("username", "").strip()
        new_email = data.get("email", "").strip()
        new_password = data.get("new_password", "").strip()
        current_password = data.get("current_password", "").strip()

        if new_username and new_username != user.username:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                errors["username"] = ["Username already taken."]
            else:
                user.username = new_username

        if new_email and new_email != user.email:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                errors["email"] = ["Email already in use."]
            else:
                user.email = new_email

        if new_password:
            if not current_password:
                errors["current_password"] = ["Current password is required to set a new password."]
            elif not user.check_password(current_password):
                errors["current_password"] = ["Current password is incorrect."]
            else:
                user.set_password(new_password)

        if errors:
            return Response(errors, status=400)

        user.save()

    return Response(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_staff": user.is_staff,
            "company_id": str(profile.company_id) if profile else None,
            "company_name": profile.company.name if profile else None,
            "role": profile.role if profile else None,
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/profile/", me_view),
    path("", include("apps.catalog.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.purchasing.urls")),
    path("", include("apps.sales.urls")),
    path("", include("apps.finance.urls")),
    path("", include("apps.marketplace.urls")),
    path("", include("apps.marketplace.shopee.urls")),
    path("", include("apps.marketplace.tiktok.urls")),
]

router = DefaultRouter()
router.register(r"company", core_views.CompanyViewSet)

urlpatterns += router.urls

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)  # type: ignore[arg-type]
