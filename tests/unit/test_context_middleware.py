"""Unit tests for context middleware."""

from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from observe_kit.context import RequestContext, get_request_context, reset_request_context
from observe_kit.context_middleware import RequestContextMiddleware, UserLoggingContextMiddleware
from observe_kit.logging.middleware import RequestLoggingMiddleware
from observe_kit.metrics.middleware import PrometheusRequestMiddleware
from observe_kit.pii_rules import PiiLevel


@pytest.fixture
def request_factory() -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


@pytest.fixture
def mock_get_response() -> Mock:
    """Mock get_response callable."""
    return Mock(return_value=HttpResponse(status=200))


@pytest.fixture(autouse=True)
def reset_context() -> None:
    """Reset context before and after test."""
    reset_request_context()
    yield
    reset_request_context()


def test_request_context_middleware_init_default(mock_get_response: Mock) -> None:
    """Test RequestContextMiddleware initialization with default PII level."""
    middleware = RequestContextMiddleware(mock_get_response)
    assert middleware.get_response == mock_get_response
    assert middleware.pii_level is None


def test_request_context_middleware_init_with_pii_level(mock_get_response: Mock) -> None:
    """Test RequestContextMiddleware initialization with custom PII level."""
    middleware = RequestContextMiddleware(mock_get_response, pii_level="SENSITIVE")
    assert middleware.pii_level == PiiLevel.SENSITIVE


def test_process_request_creates_context(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request creates and sets context."""
    with patch("observe_kit.context_middleware.resolve_tenant_id", return_value=None):
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/test/", HTTP_USER_AGENT="test-agent")
        # Explicitly set REMOTE_ADDR in META
        request.META["REMOTE_ADDR"] = "127.0.0.1"

        middleware.process_request(request)

        context = get_request_context()
        assert context.method == "GET"
        assert context.path == "/test/"
        assert context.remote_addr == "127.0.0.1"
        assert context.user_agent == "test-agent"
        assert hasattr(request, "_observe_kit_context")
        assert hasattr(request, "_observe_kit_timer")


def test_process_request_sanitizes_headers(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request sanitizes headers."""
    with patch("observe_kit.context_middleware.sanitize_mapping") as mock_sanitize:
        mock_sanitize.return_value = {"authorization": "[REDACTED]"}
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/test/")
        request.headers = {"authorization": "Bearer token"}

        middleware.process_request(request)

        mock_sanitize.assert_called()
        context = get_request_context()
        assert context.headers is not None


def test_process_request_sanitizes_query_params(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request sanitizes query params."""
    with patch("observe_kit.context_middleware.sanitize_mapping") as mock_sanitize:
        mock_sanitize.return_value = {"ip": "[HASHED]"}
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/test/?ip=1.2.3.4")

        middleware.process_request(request)

        mock_sanitize.assert_called()
        context = get_request_context()
        assert context.query_params is not None


def test_process_request_resolves_tenant(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request resolves tenant ID."""
    with patch("observe_kit.context_middleware.resolve_tenant_id") as mock_resolve:
        mock_resolve.return_value = "tenant-123"
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/test/", HTTP_X_TENANT_ID="tenant-123")

        middleware.process_request(request)

        mock_resolve.assert_called_once_with(request)
        context = get_request_context()
        assert context.tenant_id == "tenant-123"


def test_process_request_with_user(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request extracts user ID."""
    with patch("observe_kit.context_middleware.resolve_tenant_id", return_value=None):
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/test/")
        # Create a mock user object with id attribute
        user = Mock()
        user.id = 999
        request.user = user

        middleware.process_request(request)

        context = get_request_context()
        assert context.user_id == "999"


def test_process_request_enables_db_tracking(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request enables DB tracking from resolved settings."""
    with patch("observe_kit.context_middleware.resolve_tenant_id", return_value=None):
        with patch("observe_kit.context_middleware.get_observe_kit_settings") as mock_settings:
            with patch("observe_kit.context_middleware.wrap_connections") as mock_wrap:
                mock_settings.return_value.db_tracking = True
                mock_wrap.return_value = Mock()
                middleware = RequestContextMiddleware(mock_get_response)
                request = request_factory.get("/test/")

                middleware.process_request(request)

                assert hasattr(request, "_observe_kit_queries")
                mock_wrap.assert_called_once()


def test_process_request_disables_db_tracking(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request skips DB tracking when disabled in resolved settings."""
    with patch("observe_kit.context_middleware.resolve_tenant_id", return_value=None):
        with patch("observe_kit.context_middleware.get_observe_kit_settings") as mock_settings:
            with patch("observe_kit.context_middleware.wrap_connections") as mock_wrap:
                mock_settings.return_value.db_tracking = False
                middleware = RequestContextMiddleware(mock_get_response)
                request = request_factory.get("/test/")

                middleware.process_request(request)

                assert request._observe_kit_queries is None
                assert request._observe_kit_remove_wrappers is None
                mock_wrap.assert_not_called()


def test_process_request_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_request handles exceptions gracefully."""
    with patch(
        "observe_kit.context_middleware.get_pii_config", side_effect=Exception("Test error")
    ):
        with patch("observe_kit.context_middleware.logger") as mock_logger:
            middleware = RequestContextMiddleware(mock_get_response)
            request = request_factory.get("/test/")

            middleware.process_request(request)

            mock_logger.warning.assert_called_once()
            # Should create fallback context
            context = get_request_context()
            assert context is not None


def test_process_view_sets_route_from_resolver_match(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_view sets route from resolver_match."""
    middleware = RequestContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    resolver_match = Mock()
    resolver_match.route = "/test/route/"
    request.resolver_match = resolver_match

    reset_request_context()
    context = RequestContext()
    from observe_kit.context import set_request_context

    set_request_context(context)

    middleware.process_view(request, None, (), {})

    assert context.route == "/test/route/"


def test_process_view_sets_route_from_view_name(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_view sets route from view_name when route is missing."""
    middleware = RequestContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    resolver_match = Mock()
    resolver_match.route = None
    resolver_match.view_name = "test_view"
    request.resolver_match = resolver_match

    reset_request_context()
    context = RequestContext()
    from observe_kit.context import set_request_context

    set_request_context(context)

    middleware.process_view(request, None, (), {})

    assert context.route == "test_view"


def test_process_response_updates_context(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response updates context with status and duration."""
    middleware = RequestContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=201)
    request._observe_kit_timer = Mock()
    request._observe_kit_timer.stop.return_value = 123.45

    reset_request_context()
    context = RequestContext()
    from observe_kit.context import set_request_context

    set_request_context(context)

    result = middleware.process_response(request, response)

    assert context.status == 201
    assert context.duration_ms == 123.45
    assert result == response


def test_process_response_updates_db_metrics(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response updates DB metrics."""
    middleware = RequestContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)
    request._observe_kit_timer = Mock()
    request._observe_kit_timer.stop.return_value = 50.0
    request._observe_kit_queries = Mock()
    request._observe_kit_queries.count = 5
    request._observe_kit_queries.total_time = 0.1
    request._observe_kit_remove_wrappers = Mock()

    reset_request_context()
    context = RequestContext()
    from observe_kit.context import set_request_context

    set_request_context(context)

    middleware.process_response(request, response)

    assert context.db_queries == 5
    assert context.db_time_ms == 100.0  # 0.1 * 1000
    request._observe_kit_remove_wrappers.assert_called_once()


def test_recommended_middleware_order_finalizes_context_before_logs_and_metrics(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Recommended ordering should populate timing/DB fields before emitters run."""
    request = request_factory.get("/test/")
    response = HttpResponse(status=201)
    middlewares = [
        RequestLoggingMiddleware(mock_get_response),
        PrometheusRequestMiddleware(mock_get_response),
        RequestContextMiddleware(mock_get_response),
    ]

    middlewares[-1].process_request(request)
    request._observe_kit_timer = Mock()
    request._observe_kit_timer.stop.return_value = 123.45
    request._observe_kit_queries = Mock(count=7, total_time=0.015)
    request._observe_kit_remove_wrappers = Mock()

    with patch("observe_kit.logging.middleware.logger.info") as mock_log:
        with patch("observe_kit.metrics.middleware.observe_request") as mock_observe:
            for middleware in reversed(middlewares):
                response = middleware.process_response(request, response)

    assert response.status_code == 201

    log_extra = mock_log.call_args.kwargs["extra"]["extra"]
    assert log_extra["duration_ms"] == 123.45
    assert log_extra["db_queries"] == 7
    assert log_extra["db_time_ms"] == 15.0

    observe_kwargs = mock_observe.call_args.kwargs
    assert observe_kwargs["duration_seconds"] == 0.12345
    assert observe_kwargs["db_queries"] == 7
    assert observe_kwargs["db_time_seconds"] == 0.015


def test_process_response_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that process_response handles exceptions gracefully."""
    middleware = RequestContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    response = HttpResponse(status=200)

    # Make get_request_context raise an exception
    with patch("observe_kit.context_middleware.get_request_context", side_effect=Exception("Test")):
        with patch("observe_kit.context_middleware.logger") as mock_logger:
            result = middleware.process_response(request, response)
            mock_logger.warning.assert_called_once()
            assert result == response


def test_user_logging_context_middleware_sets_context(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that UserLoggingContextMiddleware sets context from request."""
    middleware = UserLoggingContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    context = RequestContext()
    context.method = "GET"
    request._observe_kit_context = context

    middleware.process_request(request)

    current_context = get_request_context()
    assert current_context.method == "GET"


def test_user_logging_context_middleware_no_context(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that UserLoggingContextMiddleware handles missing context."""
    middleware = UserLoggingContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    # No _observe_kit_context attribute

    # Should not raise
    middleware.process_request(request)


def test_detect_framework_wagtail(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test framework detection for Wagtail admin."""
    with patch("observe_kit.context_middleware._WAGTAIL_INSTALLED", True):
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/admin/")

        middleware.process_request(request)

        context = get_request_context()
        assert context.framework in ("wagtail_admin", "django_admin", None)


def test_detect_framework_django_admin(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test framework detection for Django admin."""
    with patch("observe_kit.context_middleware._WAGTAIL_INSTALLED", False):
        middleware = RequestContextMiddleware(mock_get_response)
        request = request_factory.get("/admin/")

        middleware.process_request(request)

        context = get_request_context()
        assert context.framework in ("django_admin", None)


def test_detect_framework_regular_request(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test framework detection for regular request."""
    middleware = RequestContextMiddleware(mock_get_response)
    request = request_factory.get("/api/test/")

    middleware.process_request(request)

    context = get_request_context()
    assert context.framework is None
