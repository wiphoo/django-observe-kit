from __future__ import annotations

import logging
from typing import Any, Dict

from ..conf import BODY_LOG_WARNING
from ..context import get_request_context

# Fields that should never be logged (may contain PII)
FORBIDDEN_LOG_FIELDS = {
    "body",
    "request_body",
    "response_body",
    "data",
    "payload",
    "content",
    "request_data",
    "response_data",
}


class RequestContextFilter(logging.Filter):
    """Inject request context fields into log records.

    Note: Request and response bodies are never included in logs to prevent PII exposure.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        context = get_request_context()
        for key, value in context.as_log_context().items():
            setattr(record, key, value)
        return True


def get_log_extra(event: str, **fields: Any) -> Dict[str, Any]:
    """Get log extra fields with request context.

    Automatically removes any fields that might contain request/response bodies
    to prevent PII exposure. Bodies are never logged.

    Args:
        event: Event name
        **fields: Additional fields to include

    Returns:
        Dictionary of log fields with forbidden fields removed
    """
    context = get_request_context()
    payload = {"event": event}
    payload.update(context.as_log_context())

    # Add user fields, but remove any that might contain bodies
    for key, value in fields.items():
        key_lower = key.lower()
        if any(forbidden in key_lower for forbidden in FORBIDDEN_LOG_FIELDS):
            # Silently omit body fields - don't log them at all
            continue
        payload[key] = value

    return payload


def sanitize_log_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove potentially sensitive data from log dictionaries.

    Removes fields that might contain request/response bodies or other PII.

    Args:
        data: Dictionary to sanitize

    Returns:
        Sanitized dictionary with forbidden fields removed
    """
    sanitized = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(forbidden in key_lower for forbidden in FORBIDDEN_LOG_FIELDS):
            # Replace with warning message instead of omitting completely
            sanitized[key] = BODY_LOG_WARNING
        else:
            sanitized[key] = value
    return sanitized
