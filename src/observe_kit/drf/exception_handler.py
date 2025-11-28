from __future__ import annotations

import importlib.util
import logging
from typing import Optional

from django.http import HttpRequest
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from ..logging import log_request_complete

logger = logging.getLogger(__name__)


def observed_exception_handler(exc: Exception, context: dict):
    """DRF exception handler that is PII-safe and Sentry-aware."""

    response = exception_handler(exc, context)
    request: Optional[HttpRequest] = context.get("request") if context else None
    if response is not None and response.status_code < 500:
        return response

    if response is None:
        response = Response({"detail": "Server Error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if importlib.util.find_spec("sentry_sdk") is not None:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)

    if request:
        log_request_complete(logger, exception=str(exc))
    return response
