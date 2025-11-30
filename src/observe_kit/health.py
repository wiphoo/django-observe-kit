from __future__ import annotations

import importlib.util
import logging
from typing import Any, Dict

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger(__name__)


def _check_database() -> Dict[str, Any]:
    """Check database connectivity."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return {"status": "healthy", "error": None}
    except Exception as e:
        logger.warning("Database health check failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "unhealthy", "error": str(e)}


def _check_otel() -> Dict[str, Any]:
    """Check OpenTelemetry exporter connectivity."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if provider is None:
            return {"status": "not_configured", "error": "Tracer provider not initialized"}

        # Check if provider has span processors (indicating exporter is configured)
        if hasattr(provider, "_span_processors") and provider._span_processors:
            return {"status": "healthy", "error": None}
        return {"status": "not_configured", "error": "No span processors configured"}
    except Exception as e:
        logger.warning("OTEL health check failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "unhealthy", "error": str(e)}


def _check_sentry() -> Dict[str, Any]:
    """Check Sentry connectivity."""
    try:
        if not importlib.util.find_spec("sentry_sdk"):
            return {"status": "not_configured", "error": "sentry_sdk not installed"}

        import sentry_sdk

        client = sentry_sdk.Hub.current.client
        if client is None:
            return {"status": "not_configured", "error": "Sentry client not initialized"}

        dsn = getattr(client, "dsn", None)
        if dsn:
            return {"status": "healthy", "error": None}
        return {"status": "not_configured", "error": "Sentry DSN not configured"}
    except Exception as e:
        logger.warning("Sentry health check failed", extra={"error": str(e)}, exc_info=True)
        return {"status": "unhealthy", "error": str(e)}


def healthz(  # pragma: no cover
    request: HttpRequest, detailed: bool = False
) -> HttpResponse | JsonResponse:
    """Health check endpoint.

    Args:
        request: Django request object
        detailed: If True, return detailed component status (default: False)

    Returns:
        Simple "ok" response or detailed JSON with component status
    """
    if not detailed:
        return HttpResponse("ok", content_type="text/plain")

    # Detailed health check
    components = {"database": _check_database(), "otel": _check_otel(), "sentry": _check_sentry()}

    # Overall status is healthy if all configured components are healthy
    # Components that are "not_configured" don't affect overall health
    overall_status = "healthy"
    for component, status_info in components.items():
        if status_info["status"] == "unhealthy":
            overall_status = "unhealthy"
            break

    response_data = {"status": overall_status, "components": components}

    status_code = 200 if overall_status == "healthy" else 503
    return JsonResponse(response_data, status=status_code)


def healthz_detailed(request: HttpRequest) -> JsonResponse:  # pragma: no cover
    """Detailed health check endpoint with component status."""
    return healthz(request, detailed=True)
