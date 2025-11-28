from django.urls import path

from .health import healthz
from .metrics import metrics_view

urlpatterns = [
    path("healthz", healthz),
    path("metrics", metrics_view.as_view()),
]
