from django.urls import path

from .health import healthz, healthz_detailed
from .metrics import metrics_view

urlpatterns = [
    path("healthz", healthz),
    path("healthz/detailed", healthz_detailed),
    path("metrics", metrics_view.as_view()),
]
