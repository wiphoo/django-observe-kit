from __future__ import annotations

import logging
from typing import Optional

from django.utils.deprecation import MiddlewareMixin

from .conf import DEFAULT_PII_LEVEL
from .context import RequestContext, RequestTiming, get_request_context, set_request_context
from .metrics.db import QueryRecorder, wrap_connections
from .pii_rules import PiiLevel, sanitize_headers, sanitize_query_params
from .tenant import resolve_tenant_id

logger = logging.getLogger(__name__)


class RequestContextMiddleware(MiddlewareMixin):
    """Build and store request context for each Django request."""

    def __init__(self, get_response=None, pii_level: str = DEFAULT_PII_LEVEL):
        super().__init__(get_response)
        self.pii_level = PiiLevel(pii_level)

    def process_request(self, request):
        context = RequestContext()
        context.method = request.method
        context.path = request.path
        context.remote_addr = request.META.get("REMOTE_ADDR")
        context.user_agent = request.META.get("HTTP_USER_AGENT")
        context.headers = sanitize_headers(getattr(request, "headers", {}), self.pii_level)
        context.query_params = sanitize_query_params(getattr(request, "GET", {}), self.pii_level)
        context.user_id = _safe_str(getattr(getattr(request, "user", None), "id", None))
        context.tenant_id = resolve_tenant_id(request)
        request._observe_kit_context = context
        set_request_context(context)
        request._observe_kit_timer = RequestTiming()
        request._observe_kit_queries = QueryRecorder()
        request._observe_kit_remove_wrappers = wrap_connections(request._observe_kit_queries)

    def process_view(self, request, view_func, view_args, view_kwargs):
        context = get_request_context()
        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match and resolver_match.route:
            context.route = resolver_match.route
        elif resolver_match and resolver_match.view_name:
            context.route = resolver_match.view_name

    def process_response(self, request, response):
        context = get_request_context()
        context.status = getattr(response, "status_code", None)
        context.duration_ms = (
            request._observe_kit_timer.stop() if hasattr(request, "_observe_kit_timer") else None
        )
        if hasattr(request, "_observe_kit_queries"):
            context.db_queries = request._observe_kit_queries.count
            context.db_time_ms = request._observe_kit_queries.total_time * 1000
        remover = getattr(request, "_observe_kit_remove_wrappers", None)
        if callable(remover):
            remover()
        return response


class UserLoggingContextMiddleware(MiddlewareMixin):
    """Expose the request context to all log entries during a request."""

    def process_request(self, request):
        if hasattr(request, "_observe_kit_context"):
            set_request_context(request._observe_kit_context)


def _safe_str(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    value_str = str(value)
    return value_str or None
