"""Unit tests for logging middleware."""

from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from observe_kit.context import RequestContext, reset_request_context, set_request_context
from observe_kit.logging.middleware import RequestLoggingMiddleware


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


def test_request_logging_middleware_logs_completion(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that RequestLoggingMiddleware logs request completion."""
    middleware = RequestLoggingMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    context = RequestContext()
    context.status = 200
    set_request_context(context)

    with patch("observe_kit.logging.middleware.logger") as mock_logger:
        with patch("observe_kit.logging.middleware.get_log_extra") as mock_get_extra:
            mock_get_extra.return_value = {"status": 200}
            result = middleware.process_response(request, response)

            mock_get_extra.assert_called_once_with("request_complete", status=200)
            mock_logger.info.assert_called_once_with(
                "request_complete", extra={"extra": {"status": 200}}
            )
            assert result == response


def test_request_logging_middleware_uses_response_status(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware uses response status when context status is missing."""
    middleware = RequestLoggingMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=404)

    context = RequestContext()
    context.status = None
    set_request_context(context)

    with patch("observe_kit.logging.middleware.logger"):
        with patch("observe_kit.logging.middleware.get_log_extra") as mock_get_extra:
            mock_get_extra.return_value = {"status": 404}
            middleware.process_response(request, response)

            mock_get_extra.assert_called_once_with("request_complete", status=404)


def test_request_logging_middleware_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware handles exceptions gracefully."""
    middleware = RequestLoggingMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    with patch("observe_kit.logging.middleware.get_request_context", side_effect=Exception("Test")):
        with patch("observe_kit.logging.middleware.logger") as mock_logger:
            result = middleware.process_response(request, response)

            mock_logger.warning.assert_called_once()
            assert result == response
