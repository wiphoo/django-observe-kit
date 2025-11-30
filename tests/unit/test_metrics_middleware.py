"""Unit tests for metrics middleware."""

from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from observe_kit.context import RequestContext, reset_request_context, set_request_context
from observe_kit.metrics.middleware import PrometheusRequestMiddleware


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


def test_prometheus_request_middleware_records_metrics(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that PrometheusRequestMiddleware records metrics."""
    middleware = PrometheusRequestMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    context = RequestContext()
    context.method = "GET"
    context.route = "/test/"
    context.status = 200
    context.duration_ms = 150.0
    context.tenant_id = "tenant-123"
    context.db_queries = 5
    context.db_time_ms = 50.0
    set_request_context(context)

    with patch("observe_kit.metrics.middleware.observe_request") as mock_observe:
        result = middleware.process_response(request, response)

        mock_observe.assert_called_once_with(
            method="GET",
            route="/test/",
            status=200,
            duration_seconds=0.15,
            tenant="tenant-123",
            db_queries=5,
            db_time_seconds=0.05,
        )
        assert result == response


def test_prometheus_request_middleware_uses_path_when_route_missing(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware uses path when route is missing."""
    middleware = PrometheusRequestMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    context = RequestContext()
    context.method = "GET"
    context.route = None
    context.path = "/test/"
    context.status = 200
    context.duration_ms = 100.0
    set_request_context(context)

    with patch("observe_kit.metrics.middleware.observe_request") as mock_observe:
        middleware.process_response(request, response)

        mock_observe.assert_called_once()
        assert mock_observe.call_args[1]["route"] == "/test/"


def test_prometheus_request_middleware_uses_unknown_when_route_and_path_missing(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware uses 'unknown' when both route and path are missing."""
    middleware = PrometheusRequestMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    context = RequestContext()
    context.method = "GET"
    context.route = None
    context.path = None
    context.status = 200
    context.duration_ms = 100.0
    set_request_context(context)

    with patch("observe_kit.metrics.middleware.observe_request") as mock_observe:
        middleware.process_response(request, response)

        mock_observe.assert_called_once()
        assert mock_observe.call_args[1]["route"] == "unknown"


def test_prometheus_request_middleware_uses_response_status(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware uses response status when context status is missing."""
    middleware = PrometheusRequestMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=404)

    context = RequestContext()
    context.method = "GET"
    context.route = "/test/"
    context.status = None
    context.duration_ms = 100.0
    set_request_context(context)

    with patch("observe_kit.metrics.middleware.observe_request") as mock_observe:
        middleware.process_response(request, response)

        mock_observe.assert_called_once()
        assert mock_observe.call_args[1]["status"] == 404


def test_prometheus_request_middleware_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware handles exceptions gracefully."""
    import logging

    middleware = PrometheusRequestMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    with patch("observe_kit.metrics.middleware.observe_request", side_effect=Exception("Test")):
        with patch.object(
            logging.getLogger("observe_kit.metrics.middleware"), "warning"
        ) as mock_logger:
            result = middleware.process_response(request, response)

            mock_logger.assert_called_once()
            assert result == response


def test_prometheus_request_middleware_defaults_duration(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that middleware defaults duration to 0.0 when missing."""
    middleware = PrometheusRequestMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    context = RequestContext()
    context.method = "GET"
    context.route = "/test/"
    context.status = 200
    context.duration_ms = None
    set_request_context(context)

    with patch("observe_kit.metrics.middleware.observe_request") as mock_observe:
        middleware.process_response(request, response)

        mock_observe.assert_called_once()
        assert mock_observe.call_args[1]["duration_seconds"] == 0.0
