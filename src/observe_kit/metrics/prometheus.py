from __future__ import annotations

from typing import Any, Callable, Optional

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

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


class metrics_view:  # noqa: N801 - align with Django-style class-based view
    """Simple Django view exposing Prometheus metrics."""

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Any]:
        def view(request: Any) -> Any:
            output = generate_latest()
            return cls._build_response(output)

        return view

    @staticmethod
    def _build_response(payload: bytes) -> Any:
        from django.http import HttpResponse

        return HttpResponse(payload, content_type=CONTENT_TYPE_LATEST)
