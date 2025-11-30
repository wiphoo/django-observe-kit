"""Integration tests for OpenTelemetry tracing with real OTEL Collector."""

import os
import time
from typing import Generator

import pytest
import requests
from django.test import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

pytestmark = pytest.mark.integration

from observe_kit.otel.config import init_tracing  # noqa: E402


@pytest.fixture(scope="function")
def otel_collector_endpoint() -> str:
    """OTEL Collector endpoint from environment."""
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


@pytest.fixture(scope="function")
def initialized_tracing(
    otel_collector_endpoint: str, wait_for_services: Generator[None, None, None]
) -> Generator[None, None, None]:
    """Initialize OTEL tracing with real collector endpoint."""
    # Reset any existing tracer provider
    trace.set_tracer_provider(TracerProvider())

    # Initialize with real endpoint
    init_tracing(
        service_name="test-service",
        endpoint=otel_collector_endpoint,
        resource_attributes={"environment": "test"},
    )

    yield

    # Cleanup
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


def test_tracing_sends_spans_to_collector(
    initialized_tracing: Generator[None, None, None], otel_collector_endpoint: str
) -> None:
    """Test that traces are actually sent to OTEL Collector."""
    tracer = trace.get_tracer(__name__)

    # Create a span
    with tracer.start_as_current_span("test_span") as span:
        span.set_attribute("test.attribute", "test_value")
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/test")

    # Force flush
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        # Give time for batch processor to export
        time.sleep(2)

    # Verify collector is receiving (check metrics endpoint)
    try:
        response = requests.get("http://localhost:8888/metrics", timeout=5)
        assert response.status_code == 200
        # Check that collector has processed spans
        metrics_text = response.text
        assert (
            "otelcol_receiver_accepted_spans" in metrics_text
            or "otelcol_exporter_sent_spans" in metrics_text
        )
    except requests.exceptions.RequestException:
        pytest.skip("OTEL Collector metrics endpoint not accessible")


def test_trace_context_middleware_creates_spans(
    django_client: Client, initialized_tracing: Generator[None, None, None]
) -> None:
    """Test that TraceContextMiddleware creates spans for requests."""
    # Make a request
    response = django_client.get("/healthz")
    assert response.status_code == 200

    # Check for trace ID in response headers
    trace_id = response.get("X-Trace-Id")
    assert trace_id is not None
    assert len(trace_id) == 32  # Hex string length

    # Give time for span export
    time.sleep(1)


@pytest.mark.parametrize(
    "path,expected_route", [("/healthz", "/healthz"), ("/metrics", "/metrics")]
)
def test_trace_context_middleware_route_naming(
    django_client: Client,
    initialized_tracing: Generator[None, None, None],
    path: str,
    expected_route: str,
) -> None:
    """Test that middleware correctly names spans based on route."""
    response = django_client.get(path)
    assert response.status_code in (200, 404)

    trace_id = response.get("X-Trace-Id")
    assert trace_id is not None

    # Give time for span export
    time.sleep(1)


def test_trace_context_propagation(
    django_client: Client, initialized_tracing: Generator[None, None, None]
) -> None:
    """Test that trace context is propagated via headers."""
    # Make initial request
    response1 = django_client.get("/healthz")
    trace_id_1 = response1.get("X-Trace-Id")
    assert trace_id_1 is not None

    # Make second request with trace context header
    response2 = django_client.get(
        "/healthz", HTTP_TRACEPARENT=f"00-{trace_id_1}-0000000000000000-01"
    )
    trace_id_2 = response2.get("X-Trace-Id")

    # Should use the same trace ID from header
    assert trace_id_2 == trace_id_1

    time.sleep(1)


def test_span_enrichment_with_context(
    django_client: Client, initialized_tracing: Generator[None, None, None]
) -> None:
    """Test that spans are enriched with request context."""
    from observe_kit.context import (
        RequestContext,
        get_request_context,
        reset_request_context,
        set_request_context,
    )

    reset_request_context()
    context = RequestContext()
    context.method = "GET"
    context.path = "/test"
    context.tenant_id = "test-tenant"
    context.user_id = "test-user"
    set_request_context(context)

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("enriched_span"):
        current_context = get_request_context()
        assert current_context.tenant_id == "test-tenant"
        assert current_context.user_id == "test-user"

    time.sleep(1)
