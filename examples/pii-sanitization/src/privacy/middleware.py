from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from observe_kit.context import get_request_context, set_request_context


class TraceContextSyncMiddleware(MiddlewareMixin):
    def process_request(self, request):
        span = getattr(request, "_observe_kit_span", None)
        if span is None:
            return

        span_context = span.get_span_context()
        if not span_context.is_valid:
            return

        context = get_request_context()
        context.trace_id = format(span_context.trace_id, "032x")
        context.span_id = format(span_context.span_id, "016x")
        set_request_context(context)
