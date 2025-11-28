from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from ..context import get_request_context
from .prometheus import observe_request


class PrometheusRequestMiddleware(MiddlewareMixin):
    """Record per-request HTTP and DB metrics."""

    def process_response(self, request, response):
        context = get_request_context()
        duration_ms = context.duration_ms or 0.0
        observe_request(
            method=context.method or "unknown",
            route=context.route or context.path or "unknown",
            status=context.status or getattr(response, "status_code", 500),
            duration_seconds=duration_ms / 1000,
            tenant=context.tenant_id,
            db_queries=context.db_queries,
            db_time_seconds=context.db_time_ms / 1000,
        )
        return response
