from __future__ import annotations

import hmac
import logging
import warnings
from typing import Any, Callable, Optional

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

logger = logging.getLogger(__name__)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "route", "status", "tenant"]
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "route", "status", "tenant"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

DB_QUERIES_PER_REQUEST = Histogram(
    "db_queries_per_request",
    "Database queries executed per request",
    ["route", "tenant"],
    buckets=(1, 5, 10, 25, 50, 100, 250),
)

DB_TIME_PER_REQUEST = Histogram(
    "db_time_per_request_seconds",
    "Database time spent per request",
    ["route", "tenant"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

WAGTAIL_PUBLISHED = Counter("wagtail_pages_published_total", "Wagtail pages published", ["tenant"])
WAGTAIL_UNPUBLISHED = Counter(
    "wagtail_pages_unpublished_total", "Wagtail pages unpublished", ["tenant"]
)
WAGTAIL_DELETED = Counter("wagtail_pages_deleted_total", "Wagtail pages deleted", ["tenant"])

AUDIT_EVENTS = Counter("audit_events_total", "Total audit events emitted", ["tenant"])


def observe_request(
    method: str,
    route: str,
    status: int,
    duration_seconds: float,
    tenant: Optional[str],
    db_queries: int,
    db_time_seconds: float,
) -> None:
    tenant_label = tenant or "unknown"
    status_label = str(status)
    HTTP_REQUESTS_TOTAL.labels(method, route, status_label, tenant_label).inc()
    HTTP_REQUEST_DURATION.labels(method, route, status_label, tenant_label).observe(
        duration_seconds
    )
    DB_QUERIES_PER_REQUEST.labels(route, tenant_label).observe(db_queries)
    DB_TIME_PER_REQUEST.labels(route, tenant_label).observe(db_time_seconds)


_UNAUTH_WARNING_EMITTED = False


def _reset_unauth_warning_for_tests() -> None:
    """Reset the once-per-process warning flag (test-only helper)."""
    global _UNAUTH_WARNING_EMITTED
    _UNAUTH_WARNING_EMITTED = False


def _maybe_warn_unauthenticated() -> None:
    global _UNAUTH_WARNING_EMITTED
    if _UNAUTH_WARNING_EMITTED:
        return
    try:
        from django.conf import settings as django_settings

        debug = bool(getattr(django_settings, "DEBUG", False))
    except Exception:
        debug = False
    if debug:
        return
    _UNAUTH_WARNING_EMITTED = True
    warnings.warn(
        "observe_kit /metrics endpoint is exposed without authentication "
        "(OBSERVE_KIT['METRICS_AUTH'] == 'none') while DEBUG is False. "
        "Set METRICS_AUTH to 'staff' or 'token' to prevent information disclosure.",
        RuntimeWarning,
        stacklevel=2,
    )


def _check_metrics_auth(request: Any) -> Optional[Any]:
    """Return ``None`` when the request is allowed; otherwise an HttpResponse.

    Reads ``OBSERVE_KIT['METRICS_AUTH']`` (``"none"`` | ``"staff"`` | ``"token"``).
    Token comparison uses :func:`hmac.compare_digest` and rejects empty tokens.
    """
    from django.http import HttpResponse

    from ..settings import get_observe_kit_settings

    cfg = get_observe_kit_settings()
    mode = cfg.metrics_auth

    if mode == "none":
        _maybe_warn_unauthenticated()
        return None

    if mode == "staff":
        user = getattr(request, "user", None)
        is_authenticated = bool(getattr(user, "is_authenticated", False))
        is_staff = bool(getattr(user, "is_staff", False))
        if is_authenticated and is_staff:
            return None
        return HttpResponse(status=403)

    if mode == "token":
        expected = cfg.metrics_token or ""
        header = request.META.get("HTTP_AUTHORIZATION", "")
        # HTTP auth schemes are case-insensitive per RFC 7235 §2.1, so accept
        # "Bearer", "bearer", "BEARER", and any other casing. Use str.split with
        # maxsplit=1 to tolerate any whitespace run between scheme and token.
        provided = ""
        parts = header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1]
        if expected and provided and hmac.compare_digest(expected, provided):
            return None
        return HttpResponse(status=401)

    # Unreachable: settings.py coerces unknown modes to "none".
    return None


class metrics_view:  # noqa: N801 - align with Django-style class-based view
    """Django view exposing Prometheus metrics with optional access control."""

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        def view(request: Any) -> Any:
            denied = _check_metrics_auth(request)
            if denied is not None:
                return denied
            output = generate_latest()
            return cls._build_response(output)

        return view

    @staticmethod
    def _build_response(payload: bytes) -> Any:
        from django.http import HttpResponse

        return HttpResponse(payload, content_type=CONTENT_TYPE_LATEST)
