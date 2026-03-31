"""Unit tests for OpenTelemetry middleware."""

from unittest.mock import MagicMock, Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from opentelemetry.trace import Status, StatusCode

from observe_kit.context import reset_request_context
from observe_kit.otel.middleware import TraceContextMiddleware


@pytest.fixture
def request_factory() -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


@pytest.fixture
def mock_get_response() -> Mock:
    """Mock get_response callable."""
    return Mock(return_value=HttpResponse(status=200))


@pytest.fixture
def reset_context() -> None:
    """Reset context before and after test."""
    reset_request_context()
    yield
    reset_request_context()


def test_trace_context_middleware_init(mock_get_response: Mock) -> None:
    """Test TraceContextMiddleware initialization."""
    middleware = TraceContextMiddleware(mock_get_response)
    assert middleware.get_response == mock_get_response
    # The middleware uses SpanNamer for naming spans
    assert middleware.namer is not None


def test_process_request_creates_span(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request creates a span."""
    from observe_kit.context import get_request_context

    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    middleware.process_request(request)

    assert hasattr(request, "_observe_kit_span")
    assert hasattr(request, "_observe_kit_span_context_manager")
    context = get_request_context()
    assert context.trace_id is not None
    assert context.span_id is not None


def test_process_request_extracts_trace_context(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request extracts trace context from headers."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get(
        "/test/", HTTP_TRACEPARENT="00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    )

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        # Return a valid mock context
        mock_context = Mock()
        mock_extract.return_value = mock_context

        middleware.process_request(request)

        mock_extract.assert_called_once()
        call_args = mock_extract.call_args[0][0]
        assert "traceparent" in call_args


def test_process_request_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request handles exceptions gracefully."""
    from observe_kit.context import get_request_context

    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    with patch("observe_kit.otel.middleware.extract", side_effect=Exception("Test")):
        with patch("observe_kit.otel.middleware.logger") as mock_logger:
            middleware.process_request(request)

            mock_logger.warning.assert_called_once()
            context = get_request_context()
            assert context is not None


def test_process_response_sets_status_code(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response sets status code on span."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    mock_span = Mock()
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = MagicMock()

    middleware.process_response(request, response)

    # Check that set_attribute was called with http.response.status_code (OTel semantic convention)
    calls = [
        call
        for call in mock_span.set_attribute.call_args_list
        if call[0][0] == "http.response.status_code"
    ]
    assert len(calls) > 0
    assert calls[0][0][1] == 200


def test_process_response_sets_ok_status_for_2xx_without_prior_error(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that successful responses mark spans OK when no error was recorded."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=204)
    mock_span = Mock()
    mock_span.status = Status(StatusCode.UNSET)
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = MagicMock()

    middleware.process_response(request, response)

    assert any(
        call[0][0].status_code == StatusCode.OK for call in mock_span.set_status.call_args_list
    )


def test_process_response_preserves_prior_error_status_for_2xx(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that a handled error is not overwritten by a later 2xx response."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    mock_span = Mock()
    mock_span.status = Status(StatusCode.ERROR, "boom")
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = MagicMock()

    middleware.process_response(request, response)

    assert not any(
        call[0][0].status_code == StatusCode.OK for call in mock_span.set_status.call_args_list
    )


def test_process_response_sets_error_status_for_5xx(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response sets error status for 5xx responses."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=500)
    mock_span = Mock()
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = MagicMock()

    middleware.process_response(request, response)

    mock_span.set_status.assert_called_once()
    call_args = mock_span.set_status.call_args[0][0]
    assert call_args.status_code == StatusCode.ERROR


def test_process_response_does_not_set_error_for_4xx(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response does NOT set error status for 4xx responses.

    Per OTel semantic conventions, only 5xx server errors should set ERROR status.
    4xx client errors are not server failures.
    """
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=404)
    mock_span = Mock()
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = MagicMock()

    middleware.process_response(request, response)

    # 4xx should NOT set error status per OTel semantic conventions
    mock_span.set_status.assert_not_called()


def test_process_response_enriches_span(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response enriches span."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    mock_span = Mock()
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    # Use MagicMock for context manager which has __exit__ defined
    mock_context_manager = Mock()
    mock_context_manager.__exit__ = Mock()
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = mock_context_manager

    with patch("observe_kit.otel.middleware.enrich_span") as mock_enrich:
        middleware.process_response(request, response)

        mock_enrich.assert_called_once_with(mock_span)
        # The context manager's __exit__ is called to properly end the span
        mock_context_manager.__exit__.assert_called_once()


def test_process_response_adds_trace_id_header(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response adds X-Trace-Id header."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    mock_span = Mock()
    mock_span_context = Mock()
    # Set up the span context to return a specific trace_id
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = MagicMock()

    middleware.process_response(request, response)

    # The trace ID should be formatted as 32-char hex from the span context
    assert response.get("X-Trace-Id") == "1234567890abcdef1234567890abcdef"


def test_process_response_uses_context_manager(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response uses context manager to properly end span."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    mock_span = Mock()
    mock_span_context = Mock()
    mock_span_context.trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    mock_span.get_span_context.return_value = mock_span_context
    # Use MagicMock for context manager which has __exit__ defined
    mock_context_manager = MagicMock()
    request._observe_kit_span = mock_span
    request._observe_kit_span_context_manager = mock_context_manager

    middleware.process_response(request, response)

    # Context manager's __exit__ should be called to properly detach context
    mock_context_manager.__exit__.assert_called_once_with(None, None, None)


def test_process_response_handles_missing_span(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response handles missing span gracefully."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    # No _observe_kit_span attribute

    result = middleware.process_response(request, response)

    assert result == response


def test_process_response_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response handles exceptions gracefully."""
    middleware = TraceContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    mock_span = Mock()
    mock_span.set_attribute.side_effect = Exception("Test")
    request._observe_kit_span = mock_span

    with patch("observe_kit.otel.middleware.logger") as mock_logger:
        result = middleware.process_response(request, response)

        mock_logger.warning.assert_called_once()
        assert result == response
