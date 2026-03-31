"""URL configuration for the Heroku Wagtail example."""

from __future__ import annotations

import base64
import os
from typing import Callable

from django.contrib import admin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import include, path

from observe_kit.metrics.prometheus import metrics_view as base_metrics_view

_metrics_view = base_metrics_view.as_view()
_PROM_AUTH_HEADER = None
_PROM_AUTH_REALM = "Prometheus"
_PROM_CREDS = os.getenv("PROMETHEUS_METRICS_BASIC_AUTH")
if _PROM_CREDS:
    _PROM_AUTH_HEADER = "Basic " + base64.b64encode(_PROM_CREDS.encode()).decode()


def _protected_metrics(request: HttpRequest) -> HttpResponse:
    if _PROM_AUTH_HEADER:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header != _PROM_AUTH_HEADER:
            response = HttpResponse("Authentication required", status=401)
            response["WWW-Authenticate"] = f'Basic realm="{_PROM_AUTH_REALM}"'
            return response
    return _metrics_view(request)


def _health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("cms/", include("wagtail.admin.urls")),
    path("documents/", include("wagtail.documents.urls")),
    path("metrics", _protected_metrics),
    path("healthz", _health),
    path("", include("wagtail.urls")),
]
