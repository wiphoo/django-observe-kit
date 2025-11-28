from __future__ import annotations

import logging

from django.utils.deprecation import MiddlewareMixin

from ..context import get_request_context
from .filters import get_log_extra

logger = logging.getLogger("observe_kit.request")


class RequestLoggingMiddleware(MiddlewareMixin):
    """Emit a canonical request_complete event after each response."""

    def process_response(self, request, response):
        context = get_request_context()
        extra = get_log_extra(
            "request_complete",
            status=context.status or getattr(response, "status_code", None),
        )
        logger.info("request_complete", extra={"extra": extra})
        return response
