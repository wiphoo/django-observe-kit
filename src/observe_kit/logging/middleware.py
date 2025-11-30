from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils.deprecation import MiddlewareMixin

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

from ..context import get_request_context
from .filters import get_log_extra

logger = logging.getLogger("observe_kit.request")


class RequestLoggingMiddleware(MiddlewareMixin):
    """Emit a canonical request_complete event after each response."""

    def process_response(self, request: "HttpRequest", response: "HttpResponse") -> "HttpResponse":
        try:
            context = get_request_context()
            extra = get_log_extra(
                "request_complete", status=context.status or getattr(response, "status_code", None)
            )
            logger.info("request_complete", extra={"extra": extra})
        except Exception as e:
            logger.warning(
                "Failed to log request completion", extra={"error": str(e)}, exc_info=True
            )
        return response
