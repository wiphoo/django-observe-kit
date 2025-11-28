from __future__ import annotations

import logging
from typing import Any, Dict

from ..context import get_request_context


class RequestContextFilter(logging.Filter):
    """Inject request context fields into log records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        context = get_request_context()
        for key, value in context.as_log_context().items():
            setattr(record, key, value)
        return True


def get_log_extra(event: str, **fields: Any) -> Dict[str, Any]:
    context = get_request_context()
    payload = {"event": event}
    payload.update(context.as_log_context())
    payload.update(fields)
    return payload
