"""URL configuration for drf_example project."""

from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api_app.views import PostViewSet, UserViewSet

# DRF router
router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"posts", PostViewSet, basename="post")

urlpatterns = [
    path("admin/", admin.site.urls),
    # observe_kit endpoints
    path("", include("observe_kit.urls")),
    # DRF API
    path("api/", include(router.urls)),
]





