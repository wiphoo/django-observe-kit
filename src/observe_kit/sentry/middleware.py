from __future__ import annotations

import importlib.util

from django.utils.deprecation import MiddlewareMixin

from ..context import get_request_context


class SentryContextMiddleware(MiddlewareMixin):
    """Attach observability fields to Sentry scope."""

    def process_request(self, request):
        if importlib.util.find_spec("sentry_sdk") is None:
            return None
        import sentry_sdk

        context = get_request_context()
        with sentry_sdk.configure_scope() as scope:  # type: ignore[attr-defined]
            if context.trace_id:
                scope.set_tag("otel.trace_id", context.trace_id)
            if context.tenant_id:
                scope.set_tag("tenant_id", context.tenant_id)
            if context.method:
                scope.set_tag("http.method", context.method)
            if context.path:
                scope.set_tag("http.path", context.path)
        return None
