from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from django.utils.deprecation import MiddlewareMixin
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace import context_api as trace_context_api

from ..context import get_request_context, set_request_context
from .config import SpanNamer, enrich_span

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class TraceContextMiddleware(MiddlewareMixin):
    """Ensure every request has an OpenTelemetry span and response header.

    Extracts W3C Trace-Context headers from incoming requests to support
    distributed tracing across services.
    """

    def __init__(self, get_response: Optional[Any] = None) -> None:
        super().__init__(get_response)
        self.tracer = trace.get_tracer(__name__)
        self.namer = SpanNamer()

    def process_request(self, request: "HttpRequest") -> None:
        try:
            # Extract trace context from incoming request headers
            headers = {}
            for key, value in request.META.items():
                if key.startswith("HTTP_"):
                    # Convert Django header format (HTTP_X_TRACE_ID) to standard format (x-trace-id)
                    header_name = key[5:].replace("_", "-").lower()
                    headers[header_name] = value
                elif key in ("traceparent", "tracestate"):
                    headers[key] = value

            # Extract parent context if present
            parent_context = extract(headers)

            # Create span with parent context if available
            span_name = self.namer.name_for_request(request)
            span = self.tracer.start_span(span_name, context=parent_context)

            # Set span as current for the request
            token = trace_context_api.attach(
                trace_context_api.set_span_in_context(span, parent_context)  # type: ignore[attr-defined]
            )
            request._observe_kit_span_token = token

            context = get_request_context()
            span_context = span.get_span_context()
            context.trace_id = format(span_context.trace_id, "032x")
            context.span_id = format(span_context.span_id, "016x")
            request._observe_kit_span = span
            set_request_context(context)
        except Exception as e:
            # Log error but don't break the request
            logger.warning("Failed to create trace span", extra={"error": str(e)}, exc_info=True)
            # Create a fallback context without trace info
            context = get_request_context()
            set_request_context(context)

    def process_response(self, request: "HttpRequest", response: "HttpResponse") -> "HttpResponse":
        try:
            span = getattr(request, "_observe_kit_span", None)
            if span:
                status_code = getattr(response, "status_code", None)
                span.set_attribute("http.status_code", status_code)

                # Set span status based on HTTP status code
                if status_code:
                    if status_code >= 500:
                        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
                    elif status_code >= 400:
                        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))

                enrich_span(span)
                span.end()

                # Detach span context
                token = getattr(request, "_observe_kit_span_token", None)
                if token is not None:
                    trace_context_api.detach(token)

                trace_id = get_request_context().trace_id
                if trace_id:
                    response["X-Trace-Id"] = trace_id
        except Exception as e:
            logger.warning("Failed to finalize trace span", extra={"error": str(e)}, exc_info=True)
        return response
