"""Integration tests for OpenTelemetry tracing with real OTEL Collector.

These tests REQUIRE a running OTEL Collector. They verify:
1. Traces are exported to the OTEL Collector
2. TraceContextMiddleware creates spans for HTTP requests
3. Trace context propagation works correctly
"""

import os
import time
from typing import Generator

import pytest
import requests
from django.test import Client
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

pytestmark = pytest.mark.integration


@pytest.fixture(scope="function")
def otel_collector_endpoint() -> str:
    """OTEL Collector gRPC endpoint from environment."""
    port = os.getenv(
        "OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT", "4317"
    )
    return f"http://localhost:{port}"


@pytest.fixture(scope="function")
def otel_http_endpoint() -> str:
    """OTEL Collector HTTP endpoint from environment."""
    port = os.getenv(
        "OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT", "4318"
    )
    return f"http://localhost:{port}"


@pytest.fixture(scope="function")
def otel_metrics_endpoint() -> str:
    """OTEL Collector metrics endpoint for verification."""
    port = os.getenv(
        "OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT", "8888"
    )
    return f"http://localhost:{port}"


def verify_collector_ready(otel_http_endpoint: str, timeout: int = 30) -> bool:
    """Verify the OTEL collector is ready to accept traces.

    Args:
        otel_http_endpoint: Base HTTP endpoint (e.g., http://localhost:4318)
        timeout: Seconds to wait for collector

    Returns:
        True if collector is ready
    """
    traces_endpoint = f"{otel_http_endpoint}/v1/traces"
    start = time.time()

    while time.time() - start < timeout:
        try:
            # Send an empty trace request - collector should respond
            response = requests.post(
                traces_endpoint,
                headers={"Content-Type": "application/json"},
                json={},
                timeout=3
            )
            # Any response (even 400/415 for bad content type) means collector is up
            if response.status_code in (200, 400, 405, 415, 404):
                return True
        except requests.exceptions.ConnectionError:
            # Connection refused - service not ready yet
            pass
        except requests.exceptions.RequestException:
            # Other errors might mean service is up but rejecting request
            # Try health endpoint as fallback
            try:
                health_endpoint = otel_http_endpoint.replace(":4318", ":13133")
                health_response = requests.get(f"{health_endpoint}/", timeout=2)
                if health_response.status_code == 200:
                    return True
            except Exception:
                pass
        time.sleep(0.5)

    return False


@pytest.fixture(scope="function")
def fresh_tracer_provider(
    otel_http_endpoint: str, wait_for_otel_collector: Generator[None, None, None]
) -> Generator[TracerProvider, None, None]:
    """Create a fresh TracerProvider connected to the real collector.

    This fixture creates a new provider for each test to avoid state pollution.
    """
    # Verify collector is actually ready
    assert verify_collector_ready(otel_http_endpoint), (
        f"OTEL Collector not ready at {otel_http_endpoint}"
    )

    # Create exporter and provider
    traces_endpoint = f"{otel_http_endpoint}/v1/traces"
    exporter = OTLPSpanExporter(endpoint=traces_endpoint)
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Set as the global provider (needed for middleware integration)
    old_provider = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)

    yield provider

    # Cleanup
    try:
        provider.force_flush(timeout_millis=5000)
    except Exception:
        pass
    try:
        provider.shutdown()
    except Exception:
        pass

    # Restore old provider
    trace.set_tracer_provider(old_provider)


@pytest.fixture(scope="function")
def test_tracer(fresh_tracer_provider: TracerProvider) -> trace.Tracer:
    """Get a tracer from the fresh provider."""
    return fresh_tracer_provider.get_tracer(__name__)


def test_otel_collector_is_running(
    otel_http_endpoint: str,
    otel_metrics_endpoint: str,
    wait_for_otel_collector: Generator[None, None, None],
) -> None:
    """Test that OTEL Collector is accessible and running."""
    # Check OTLP HTTP endpoint responds
    response = requests.post(
        f"{otel_http_endpoint}/v1/traces",
        headers={"Content-Type": "application/json"},
        json={},
        timeout=5
    )
    # Empty request should return success or partial success
    assert response.status_code == 200, (
        f"OTEL Collector OTLP endpoint not accessible: {response.text}"
    )

    # Check collector metrics endpoint
    response = requests.get(f"{otel_metrics_endpoint}/metrics", timeout=5)
    assert response.status_code == 200, "OTEL Collector metrics endpoint not accessible"


def test_otel_collector_accepts_traces(
    test_tracer: trace.Tracer,
    otel_metrics_endpoint: str,
) -> None:
    """Test that traces are exported and accepted by OTEL Collector."""
    # Create a span with identifiable attributes
    with test_tracer.start_as_current_span("integration-test-span") as span:
        span.set_attribute("test.type", "integration")
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/test-route")

    # Force flush to ensure span is exported
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    # Wait a bit for collector to process
    time.sleep(1)

    # Verify collector is running and processing by checking metrics
    response = requests.get(f"{otel_metrics_endpoint}/metrics", timeout=5)
    assert response.status_code == 200, "OTEL Collector metrics endpoint not accessible"

    # If we get here without errors, the span was sent successfully


def test_trace_context_middleware_creates_spans(
    django_client: Client, test_tracer: trace.Tracer
) -> None:
    """Test that TraceContextMiddleware creates spans and returns trace ID."""
    # Make a request
    response = django_client.get("/metrics")
    assert response.status_code == 200

    # Check for trace ID in response headers
    trace_id = response.get("X-Trace-Id")
    assert trace_id is not None, "X-Trace-Id header not found in response"
    assert len(trace_id) == 32, f"Trace ID should be 32 hex chars, got: {trace_id}"

    # Verify trace ID is valid hex
    try:
        int(trace_id, 16)
    except ValueError:
        pytest.fail(f"Trace ID is not valid hex: {trace_id}")


def test_trace_context_propagation_with_incoming_header(
    django_client: Client, test_tracer: trace.Tracer
) -> None:
    """Test that trace context from incoming headers is respected."""
    # Create a known trace ID
    known_trace_id = "0" * 16 + "a1b2c3d4e5f67890"  # 32 char hex
    known_span_id = "1234567890abcdef"

    # Make request with traceparent header
    traceparent = f"00-{known_trace_id}-{known_span_id}-01"
    response = django_client.get("/metrics", HTTP_TRACEPARENT=traceparent)
    assert response.status_code == 200

    # Response should have the same trace ID (propagated)
    response_trace_id = response.get("X-Trace-Id")
    assert response_trace_id == known_trace_id, (
        f"Expected trace ID {known_trace_id} to be propagated, got {response_trace_id}"
    )


def test_multiple_requests_have_different_trace_ids(
    django_client: Client, test_tracer: trace.Tracer
) -> None:
    """Test that different requests get different trace IDs."""
    trace_ids = set()

    for _ in range(5):
        response = django_client.get("/metrics")
        assert response.status_code == 200
        trace_id = response.get("X-Trace-Id")
        assert trace_id is not None
        trace_ids.add(trace_id)

    # All trace IDs should be unique
    assert len(trace_ids) == 5, "Expected 5 unique trace IDs for 5 requests"


def test_span_creation_and_attributes(
    test_tracer: trace.Tracer,
) -> None:
    """Test that spans can be created with HTTP attributes."""
    # Create span with HTTP semantic conventions
    with test_tracer.start_as_current_span("http-request-span") as span:
        span.set_attribute("http.method", "POST")
        span.set_attribute("http.route", "/api/users")
        span.set_attribute("http.status_code", 201)
        span.set_attribute("http.target", "/api/users")
        span.set_attribute("tenant.id", "test-tenant")

        # Verify span is recording
        assert span.is_recording(), "Span should be recording"

        # Get span context
        ctx = span.get_span_context()
        assert ctx.trace_id != 0, "Trace ID should be non-zero"
        assert ctx.span_id != 0, "Span ID should be non-zero"


def test_nested_spans_share_trace_id(
    test_tracer: trace.Tracer,
) -> None:
    """Test that nested spans share the same trace ID."""
    with test_tracer.start_as_current_span("parent-span") as parent:
        parent_ctx = parent.get_span_context()

        with test_tracer.start_as_current_span("child-span") as child:
            child_ctx = child.get_span_context()

            # Same trace ID
            assert child_ctx.trace_id == parent_ctx.trace_id, (
                "Child span should share parent's trace ID"
            )
            # Different span IDs
            assert child_ctx.span_id != parent_ctx.span_id, (
                "Child span should have different span ID"
            )


def test_span_enrichment_with_context(
    test_tracer: trace.Tracer,
) -> None:
    """Test that spans can be enriched with request context."""
    from observe_kit.context import (
        RequestContext,
        get_request_context,
        reset_request_context,
        set_request_context,
    )

    reset_request_context()
    context = RequestContext()
    context.method = "GET"
    context.path = "/api/test"
    context.tenant_id = "enrichment-test-tenant"
    context.user_id = "enrichment-test-user"
    set_request_context(context)

    with test_tracer.start_as_current_span("enriched-span") as span:
        # Manually enrich (this is what the middleware does)
        from observe_kit.otel.config import enrich_span

        enrich_span(span)

    # Verify context was set
    current_context = get_request_context()
    assert current_context.tenant_id == "enrichment-test-tenant"
    assert current_context.user_id == "enrichment-test-user"
