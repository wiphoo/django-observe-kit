from __future__ import annotations

import logging
from typing import Dict, Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

from ..context import get_request_context

logger = logging.getLogger(__name__)


def init_tracing(
    service_name: str,
    resource_attributes: Optional[Dict[str, str]] = None,
    endpoint: Optional[str] = None,
) -> None:
    """Configure the OpenTelemetry SDK with an OTLP HTTP exporter."""

    attributes = {"service.name": service_name, **(resource_attributes or {})}
    provider = TracerProvider(resource=Resource.create(attributes))
    exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    logger.info("otel tracer configured", extra={"service": service_name, "endpoint": endpoint})


def enrich_span(span: Span) -> None:
    context = get_request_context()
    for key, value in context.as_attributes().items():
        if value is not None:
            span.set_attribute(key, value)


class SpanNamer:
    """Apply human-friendly names to request spans."""

    def __init__(self, default_route: str = "unknown") -> None:
        self.default_route = default_route

    def name_for_request(self, request) -> str:
        route = getattr(request, "resolver_match", None)
        if route and getattr(route, "route", None):
            return str(route.route)
        if route and getattr(route, "view_name", None):
            return str(route.view_name)
        return getattr(request, "path", self.default_route)
