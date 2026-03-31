from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry._logs import get_logger_provider, set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

from ..context import get_request_context

logger = logging.getLogger(__name__)
_TRACING_INITIALIZED = False
_LOG_EXPORT_INITIALIZED = False
_OTEL_LOG_HANDLER_ATTR = "_observe_kit_otel_handler"


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


def _init_otel_log_export(resource: Resource, endpoint: Optional[str]) -> None:
    """Wire up an OTEL log exporter so Python log records are shipped alongside traces.

    Uses the same resource attributes and base endpoint as the tracer so that
    log records in ClickHouse's ``otel_logs`` table carry a matching
    ``ServiceName`` and can be correlated with traces via ``TraceId``.

    The exporter appends ``/v1/logs`` to the base endpoint automatically,
    mirroring how ``OTLPSpanExporter`` appends ``/v1/traces``.
    """
    global _LOG_EXPORT_INITIALIZED

    if _LOG_EXPORT_INITIALIZED:
        logger.debug("otel log export already configured")
        return

    log_exporter = OTLPLogExporter(endpoint=endpoint) if endpoint else OTLPLogExporter()
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    current_provider = get_logger_provider()
    if not isinstance(current_provider, LoggerProvider):
        set_logger_provider(log_provider)
    else:
        log_provider = current_provider
    # Bridge Python's standard logging into the OTEL SDK.
    root_logger = logging.getLogger()
    has_handler = any(
        isinstance(handler, LoggingHandler) and getattr(handler, _OTEL_LOG_HANDLER_ATTR, False)
        for handler in root_logger.handlers
    )
    if not has_handler:
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=log_provider)
        setattr(handler, _OTEL_LOG_HANDLER_ATTR, True)
        root_logger.addHandler(handler)
    _LOG_EXPORT_INITIALIZED = True
    logger.info("otel log export configured", extra={"endpoint": endpoint})


def init_tracing(
    service_name: str,
    resource_attributes: Optional[Dict[str, str]] = None,
    endpoint: Optional[str] = None,
) -> None:
    """Configure the OpenTelemetry SDK with an OTLP HTTP exporter.

    Also sets up an OTEL log exporter (same endpoint, same resource) so that
    Python log records flow into ClickHouse's ``otel_logs`` table and appear
    in HyperDX alongside traces, correlated by ``TraceId``.

    Args:
        service_name: Name of the service (required, alphanumeric with
                      hyphens/underscores, max 255 chars)
        resource_attributes: Optional additional resource attributes
        endpoint: Optional OTLP base endpoint URL (http/https). Exporters
                  append ``/v1/traces`` and ``/v1/logs`` automatically.

    Raises:
        ConfigurationError: If any configuration parameter is invalid
    """
    global _TRACING_INITIALIZED

    # Validate configuration
    _validate_service_name(service_name)
    _validate_endpoint(endpoint)
    _validate_resource_attributes(resource_attributes)

    if _TRACING_INITIALIZED:
        logger.debug("otel tracer already configured", extra={"service": service_name})
        return

    attributes = {"service.name": service_name, **(resource_attributes or {})}
    resource = Resource.create(attributes)

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    logger.info("otel tracer configured", extra={"service": service_name, "endpoint": endpoint})

    _init_otel_log_export(resource=resource, endpoint=endpoint)
    _TRACING_INITIALIZED = True


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
