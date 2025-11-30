from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Optional

from django.utils.deprecation import MiddlewareMixin

if TYPE_CHECKING:
    from django.http import HttpRequest

from ..context import get_request_context


class SentryContextMiddleware(MiddlewareMixin):
    """Attach observability fields to Sentry scope."""

    def process_request(self, request: "HttpRequest") -> Optional[None]:
        try:
            if importlib.util.find_spec("sentry_sdk") is None:
                return None
            import sentry_sdk

            context = get_request_context()
            with sentry_sdk.configure_scope() as scope:
                if context.trace_id:
                    scope.set_tag("otel.trace_id", context.trace_id)
                if context.tenant_id:
                    scope.set_tag("tenant_id", context.tenant_id)
                if context.method:
                    scope.set_tag("http.method", context.method)
                if context.path:
                    scope.set_tag("http.path", context.path)
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning("Failed to set Sentry context", extra={"error": str(e)}, exc_info=True)
        return None
