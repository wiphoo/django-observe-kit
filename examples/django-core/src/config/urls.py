from django.urls import include, path

from core.views import health, home

urlpatterns = [
    path("", home),
    path("healthz", health),
    path("", include("observe_kit.urls")),
]
