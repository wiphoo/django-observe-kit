"""Tests for trace context propagation."""

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("django"), reason="django not installed"
)


def test_trace_context_middleware_imports() -> None:
    """Test that trace context middleware can be imported."""
    from observe_kit.otel.middleware import TraceContextMiddleware

    assert TraceContextMiddleware is not None


def test_trace_context_extraction() -> None:
    """Test that trace context can be extracted from headers."""
    from opentelemetry.propagate import extract

    # Simulate W3C traceparent header
    headers = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}

    # Should not raise
    context = extract(headers)
    assert context is not None


def test_span_namer() -> None:
    """Test span naming."""
    from observe_kit.otel.config import SpanNamer

    namer = SpanNamer()
    assert namer.default_route == "unknown"

    # Mock request object
    class MockRequest:
        path = "/test/path"

    request = MockRequest()
    name = namer.name_for_request(request)
    assert name == "/test/path"
