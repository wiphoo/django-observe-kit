"""URL configuration for django_example project."""

from django.contrib import admin
from django.urls import include, path

from example_app import views

urlpatterns = [
    path("admin/", admin.site.urls),
    # observe_kit endpoints
    path("", include("observe_kit.urls")),
    # Example views
    path("", views.index, name="index"),
    path("users/", views.user_list, name="user_list"),
    path("slow/", views.slow_view, name="slow_view"),
]





