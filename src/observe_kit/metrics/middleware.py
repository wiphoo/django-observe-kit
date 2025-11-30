from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.deprecation import MiddlewareMixin

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

from ..context import get_request_context
from .prometheus import observe_request


class PrometheusRequestMiddleware(MiddlewareMixin):
    """Record per-request HTTP and DB metrics."""

    def process_response(self, request: "HttpRequest", response: "HttpResponse") -> "HttpResponse":
        try:
            context = get_request_context()
            duration_ms = context.duration_ms or 0.0
            status_code = context.status or getattr(response, "status_code", 500)
            observe_request(
                method=context.method or "unknown",
                route=context.route or context.path or "unknown",
                status=int(status_code) if status_code is not None else 500,
                duration_seconds=duration_ms / 1000,
                tenant=context.tenant_id,
                db_queries=context.db_queries,
                db_time_seconds=context.db_time_ms / 1000,
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning("Failed to record metrics", extra={"error": str(e)}, exc_info=True)
        return response
