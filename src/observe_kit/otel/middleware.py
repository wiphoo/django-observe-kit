from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin
from opentelemetry import trace

from ..context import get_request_context, set_request_context
from .config import SpanNamer, enrich_span


class TraceContextMiddleware(MiddlewareMixin):
    """Ensure every request has an OpenTelemetry span and response header."""

    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.tracer = trace.get_tracer(__name__)
        self.namer = SpanNamer()

    def process_request(self, request):
        span_name = self.namer.name_for_request(request)
        span = self.tracer.start_span(span_name)
        context = get_request_context()
        context.trace_id = format(span.get_span_context().trace_id, "032x")
        context.span_id = format(span.get_span_context().span_id, "016x")
        request._observe_kit_span = span
        set_request_context(context)

    def process_response(self, request, response):
        span = getattr(request, "_observe_kit_span", None)
        if span:
            span.set_attribute("http.status_code", getattr(response, "status_code", None))
            enrich_span(span)
            span.end()
            response["X-Trace-Id"] = get_request_context().trace_id or ""
        return response
