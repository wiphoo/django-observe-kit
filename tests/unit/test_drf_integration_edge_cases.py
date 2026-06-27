"""Edge case tests for DRF integration."""

from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


@pytest.fixture
def request_factory() -> RequestFactory:
    """Create a request factory."""
    from django.test import RequestFactory

    return RequestFactory()


def test_detect_drf_route_no_resolver_match(request_factory: RequestFactory) -> None:
    """Test detect_drf_route when request has no resolver_match."""
    from observe_kit.drf.integration import detect_drf_route

    request = request_factory.get("/test")
    # Ensure no resolver_match
    if hasattr(request, "resolver_match"):
        delattr(request, "resolver_match")

    result = detect_drf_route(request)
    assert result is None


def test_detect_drf_route_no_view_func(request_factory: RequestFactory) -> None:
    """Test detect_drf_route when resolver_match has no func."""
    from observe_kit.drf.integration import detect_drf_route

    request = request_factory.get("/test")
    mock_resolver = Mock()
    mock_resolver.func = None
    request.resolver_match = mock_resolver

    result = detect_drf_route(request)
    assert result is None


def test_detect_drf_route_no_view_cls(request_factory: RequestFactory) -> None:
    """Test detect_drf_route when view_func has no cls."""
    from observe_kit.drf.integration import detect_drf_route

    request = request_factory.get("/test")
    mock_resolver = Mock()
    mock_view_func = Mock()
    mock_view_func.cls = None
    mock_resolver.func = mock_view_func
    request.resolver_match = mock_resolver

    result = detect_drf_route(request)
    assert result is None


def test_detect_drf_route_not_viewset(request_factory: RequestFactory) -> None:
    """Test detect_drf_route when view_cls is not a ViewSet."""
    from observe_kit.drf.integration import detect_drf_route

    request = request_factory.get("/test")
    mock_resolver = Mock()
    mock_view_func = Mock()
    mock_view_func.cls = type("NotAViewSet", (), {})
    mock_resolver.func = mock_view_func
    request.resolver_match = mock_resolver

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=Mock()):
        from rest_framework.viewsets import ViewSet

        # Make sure it's not a subclass
        assert not issubclass(mock_view_func.cls, ViewSet)

        result = detect_drf_route(request)
        assert result is None


def test_detect_drf_route_actions_dict(request_factory: RequestFactory) -> None:
    """Test detect_drf_route with actions dict in view_func."""
    from observe_kit.drf.integration import detect_drf_route

    request = request_factory.post("/test")
    mock_resolver = Mock()
    mock_resolver.kwargs = {}
    mock_view_func = Mock()
    mock_view_func.actions = {"post": "custom_action"}

    # Create a proper ViewSet subclass
    from rest_framework.viewsets import ViewSet

    class TestViewSet(ViewSet):
        pass

    mock_view_func.cls = TestViewSet
    mock_resolver.func = mock_view_func
    request.resolver_match = mock_resolver

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=Mock()):
        result = detect_drf_route(request)
        assert result == "drf.TestViewSet.custom_action"


def test_detect_drf_route_generic_viewset_subclass(request_factory: RequestFactory) -> None:
    """Test detect_drf_route with a GenericViewSet subclass from DRF routers."""
    from observe_kit.drf.integration import detect_drf_route

    request = request_factory.get("/test/")
    mock_resolver = Mock()
    mock_resolver.kwargs = {}
    mock_view_func = Mock()

    from rest_framework.viewsets import GenericViewSet

    class TestGenericViewSet(GenericViewSet):
        queryset = []

    mock_view_func.cls = TestGenericViewSet
    mock_view_func.actions = {"get": "list"}
    mock_resolver.func = mock_view_func
    request.resolver_match = mock_resolver

    with patch("observe_kit.drf.integration.importlib.util.find_spec", return_value=Mock()):
        result = detect_drf_route(request)
        assert result == "drf.TestGenericViewSet.list"


def test_set_drf_action_preserves_existing() -> None:
    """Test that set_drf_action preserves existing route if new is None."""
    from observe_kit.context import (
        RequestContext,
        get_request_context,
        reset_request_context,
        set_request_context,
    )
    from observe_kit.drf.integration import set_drf_action

    reset_request_context()
    context = RequestContext()
    context.route = "existing-route"
    set_request_context(context)

    set_drf_action(None)

    updated_context = get_request_context()
    assert updated_context.route == "existing-route"
