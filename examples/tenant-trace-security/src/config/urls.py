from django.urls import include, path

from core.views import context_view

urlpatterns = [
    path("", context_view),
    path("", include("observe_kit.urls")),
]
