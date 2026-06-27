"""Unit tests for DRF integration."""

from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory

from observe_kit.context import (
    RequestContext,
    get_request_context,
    reset_request_context,
    set_request_context,
)


@pytest.fixture
def request_factory() -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


@pytest.fixture
def mock_get_response() -> Mock:
    """Mock get_response callable."""
    return Mock(return_value=Mock())


@pytest.fixture
def reset_context() -> None:
    """Reset context before and after test."""
    reset_request_context()
    yield
    reset_request_context()


def test_detect_drf_route_no_rest_framework(request_factory: RequestFactory) -> None:
    """Test that detect_drf_route returns None when rest_framework is not installed."""
    from observe_kit.drf.integration import detect_drf_route  # noqa: F401

    request = request_factory.get("/test/")

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=None):
        result = detect_drf_route(request)

        assert result is None


def test_detect_drf_route_from_view_instance(request_factory: RequestFactory) -> None:
    """Test that detect_drf_route detects route from view instance."""
    from observe_kit.drf.integration import detect_drf_route  # noqa: F401

    request = request_factory.get("/test/")

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=Mock()):
        # Create a mock ViewSet class
        class MockViewSetMixin:
            pass

        mock_viewset = MockViewSetMixin()
        mock_viewset.__class__.__name__ = "TestViewSet"
        mock_viewset.action = "list"
        request.view = mock_viewset

        # Mock the ViewSetMixin import inside the function
        with patch("rest_framework.viewsets.ViewSetMixin"):
            # Make isinstance check work
            import rest_framework.viewsets

            original_viewset_mixin = rest_framework.viewsets.ViewSetMixin
            rest_framework.viewsets.ViewSetMixin = MockViewSetMixin
            try:
                result = detect_drf_route(request)
                assert result == "drf.TestViewSet.list"
            finally:
                rest_framework.viewsets.ViewSetMixin = original_viewset_mixin


def test_detect_drf_route_from_resolver_match(request_factory: RequestFactory) -> None:
    """Test that detect_drf_route detects route from resolver_match."""
    from observe_kit.drf.integration import detect_drf_route  # noqa: F401

    request = request_factory.get("/test/")

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=Mock()):
        # Create a mock ViewSet class
        class MockViewSetMixin:
            pass

        class UserViewSet(MockViewSetMixin):
            pass

        mock_view_func = Mock()
        mock_view_func.cls = UserViewSet
        mock_resolver_match = Mock()
        mock_resolver_match.func = mock_view_func
        mock_resolver_match.kwargs = {}
        request.resolver_match = mock_resolver_match

        # Mock the ViewSetMixin import
        import rest_framework.viewsets

        original_viewset_mixin = rest_framework.viewsets.ViewSetMixin
        rest_framework.viewsets.ViewSetMixin = MockViewSetMixin
        try:
            result = detect_drf_route(request)
            assert result == "drf.UserViewSet.list"
        finally:
            rest_framework.viewsets.ViewSetMixin = original_viewset_mixin


def test_detect_drf_route_handles_exception(request_factory: RequestFactory) -> None:
    """Test that detect_drf_route handles exceptions gracefully."""
    from observe_kit.drf.integration import detect_drf_route  # noqa: F401

    request = request_factory.get("/test/")
    request.view = Mock()
    request.view.action = "list"

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=Mock()):
        # Make the ViewSetMixin import raise an exception
        with patch("rest_framework.viewsets.ViewSetMixin", side_effect=Exception("Test")):
            with patch("observe_kit.drf.integration.logger") as mock_logger:
                result = detect_drf_route(request)

                mock_logger.debug.assert_called_once()
                assert result is None


def test_set_drf_action(reset_context: None) -> None:
    """Test that set_drf_action sets route on context."""
    from observe_kit.drf.integration import set_drf_action  # noqa: F401

    context = RequestContext()
    set_request_context(context)

    set_drf_action("drf.UserViewSet.list")

    assert context.route == "drf.UserViewSet.list"


def test_set_drf_action_preserves_existing_route(reset_context: None) -> None:
    """Test that set_drf_action preserves existing route if new route is None."""
    from observe_kit.drf.integration import set_drf_action  # noqa: F401

    context = RequestContext()
    context.route = "existing-route"
    set_request_context(context)

    set_drf_action(None)

    assert context.route == "existing-route"


def test_drf_integration_middleware_process_view(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that DRFIntegrationMiddleware detects and sets DRF route."""
    from observe_kit.drf.integration import DRFIntegrationMiddleware  # noqa: F401

    middleware = DRFIntegrationMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    with patch("observe_kit.drf.integration.detect_drf_route", return_value="drf.UserViewSet.list"):
        middleware.process_view(request, None, (), {})

        context = get_request_context()
        assert context.route == "drf.UserViewSet.list"


def test_drf_integration_middleware_updates_span(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that DRFIntegrationMiddleware updates span name."""
    from observe_kit.drf.integration import DRFIntegrationMiddleware  # noqa: F401

    middleware = DRFIntegrationMiddleware(mock_get_response)
    request = request_factory.get("/test/")
    mock_span = Mock()
    request._observe_kit_span = mock_span

    with patch("observe_kit.drf.integration.detect_drf_route", return_value="drf.UserViewSet.list"):
        middleware.process_view(request, None, (), {})

        mock_span.update_name.assert_called_once_with("drf.UserViewSet.list")


def test_drf_integration_middleware_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that DRFIntegrationMiddleware handles exceptions gracefully."""
    from observe_kit.drf.integration import DRFIntegrationMiddleware  # noqa: F401

    middleware = DRFIntegrationMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    with patch("observe_kit.drf.integration.detect_drf_route", side_effect=Exception("Test")):
        with patch("observe_kit.drf.integration.logger") as mock_logger:
            middleware.process_view(request, None, (), {})

            mock_logger.warning.assert_called_once()
