from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, MutableMapping, Optional

_logger = logging.getLogger(__name__)


@dataclass
class RequestContext:
    """PII-safe request-scoped context used across logging, tracing, and metrics."""

    method: Optional[str] = None
    path: Optional[str] = None
    route: Optional[str] = None
    status: Optional[int] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    remote_addr: Optional[str] = None
    user_agent: Optional[str] = None
    query_params: MutableMapping[str, Any] = field(default_factory=dict)
    headers: MutableMapping[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.perf_counter)
    duration_ms: Optional[float] = None
    db_queries: int = 0
    db_time_ms: float = 0.0
    framework: Optional[str] = None  # e.g., "wagtail_admin", "django", "drf"
    attributes: Dict[str, Any] = field(default_factory=dict)

    def as_log_context(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "route": self.route,
            "status": self.status,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "duration_ms": self.duration_ms,
            "db_queries": self.db_queries,
            "db_time_ms": self.db_time_ms,
        }

    def as_attributes(self) -> Dict[str, Any]:
        attrs = {
            "http.method": self.method,
            "http.path": self.path,
            "http.route": self.route,
            "http.status_code": self.status,
            "tenant.id": self.tenant_id,
            "enduser.id": self.user_id,
            "db.query.count": self.db_queries,
            "db.query.time_ms": self.db_time_ms,
        }
        if self.framework:
            attrs["framework"] = self.framework
        return attrs


_request_context: contextvars.ContextVar[RequestContext] = contextvars.ContextVar(
    "observe_kit_request_context"
)


def get_request_context(default: Optional[RequestContext] = None) -> RequestContext:
    try:
        return _request_context.get()
    except LookupError:
        _logger.debug(
            "observe_kit: get_request_context called outside a request context; "
            "returning a blank RequestContext"
        )
        if default is None:
            default = RequestContext()
        _request_context.set(default)
        return default


def set_request_context(context: RequestContext) -> None:
    _request_context.set(context)


def reset_request_context() -> None:
    _request_context.set(RequestContext())


class RequestTiming:
    """Helper to measure elapsed time in milliseconds."""

    def __init__(self) -> None:
        self.start = time.perf_counter()

    def stop(self) -> float:
        return (time.perf_counter() - self.start) * 1000
