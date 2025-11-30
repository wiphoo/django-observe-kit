from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

from ..context import get_request_context

logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when configuration is invalid."""

    pass


def _validate_service_name(service_name: str) -> None:
    """Validate service name."""
    if not service_name or not isinstance(service_name, str):
        raise ConfigurationError("service_name must be a non-empty string")
    if len(service_name) > 255:
        raise ConfigurationError("service_name must be 255 characters or less")
    if not service_name.replace("_", "").replace("-", "").isalnum():
        raise ConfigurationError(
            "service_name must contain only alphanumeric characters, hyphens, and underscores"
        )


def _validate_endpoint(endpoint: Optional[str]) -> None:
    """Validate OTLP endpoint URL."""
    if endpoint is None:
        return
    if not isinstance(endpoint, str):
        raise ConfigurationError("endpoint must be a string or None")
    try:
        parsed = urlparse(endpoint)
        if not parsed.scheme:
            raise ConfigurationError(
                "endpoint must be a valid URL with scheme (http:// or https://)"
            )
        if parsed.scheme not in ("http", "https"):
            raise ConfigurationError("endpoint scheme must be http or https")
    except Exception as e:
        if isinstance(e, ConfigurationError):
            raise
        raise ConfigurationError(f"endpoint must be a valid URL: {e}") from e


def _validate_resource_attributes(resource_attributes: Optional[Dict[str, str]]) -> None:
    """Validate resource attributes."""
    if resource_attributes is None:
        return
    if not isinstance(resource_attributes, dict):
        raise ConfigurationError("resource_attributes must be a dictionary")
    for key, value in resource_attributes.items():
        if not isinstance(key, str):
            raise ConfigurationError(
                f"resource_attributes keys must be strings, got {type(key).__name__}"
            )
        if not isinstance(value, str):
            raise ConfigurationError(
                f"resource_attributes values must be strings, "
                f"got {type(value).__name__} for key '{key}'"
            )


def init_tracing(
    service_name: str,
    resource_attributes: Optional[Dict[str, str]] = None,
    endpoint: Optional[str] = None,
) -> None:
    """Configure the OpenTelemetry SDK with an OTLP HTTP exporter.

    Args:
        service_name: Name of the service (required, alphanumeric with
                      hyphens/underscores, max 255 chars)
        resource_attributes: Optional additional resource attributes
        endpoint: Optional OTLP endpoint URL (must be valid http/https URL)

    Raises:
        ConfigurationError: If any configuration parameter is invalid
    """
    # Validate configuration
    _validate_service_name(service_name)
    _validate_endpoint(endpoint)
    _validate_resource_attributes(resource_attributes)

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
    """Apply human-friendly names to request spans.

    Supports DRF ViewSet naming format: 'drf.<ViewSet>.<action>'
    """

    def __init__(self, default_route: str = "unknown") -> None:
        self.default_route = default_route

    def name_for_request(self, request: Any) -> str:
        # First check if context already has a route (e.g., from DRF detection)
        try:
            from ..context import get_request_context

            context = get_request_context()
            if context.route:
                return context.route
        except Exception:
            pass

        # Fallback to resolver_match
        route = getattr(request, "resolver_match", None)
        if route and getattr(route, "route", None):
            return str(route.route)
        if route and getattr(route, "view_name", None):
            return str(route.view_name)
        return getattr(request, "path", self.default_route)

    def update_span_name(self, span: Any, request: Any) -> None:
        """Update span name if route was detected later (e.g., in process_view)."""
        try:
            from ..context import get_request_context

            context = get_request_context()
            if context.route:
                span.update_name(context.route)
        except Exception:
            pass
