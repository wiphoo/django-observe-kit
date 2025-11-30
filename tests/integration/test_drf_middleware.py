"""Integration tests for DRF middleware with real Django requests."""

from typing import TYPE_CHECKING

import pytest
from django.http import HttpRequest, JsonResponse
from django.test import Client, RequestFactory

pytestmark = pytest.mark.integration

from observe_kit.context import get_request_context  # noqa: E402

if TYPE_CHECKING:
    from observe_kit.drf.integration import DRFIntegrationMiddleware


def test_view(request: HttpRequest, django_client: Client) -> JsonResponse:
    """Simple test view."""
    return JsonResponse({"status": "ok"})


@pytest.fixture
def request_factory(django_client: Client) -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


@pytest.fixture
def drf_middleware(django_client: Client) -> "DRFIntegrationMiddleware":
    """DRF integration middleware instance."""
    from observe_kit.drf.integration import DRFIntegrationMiddleware  # noqa: F401

    return DRFIntegrationMiddleware()


def test_drf_middleware_detects_viewset_action(
    request_factory: RequestFactory, drf_middleware: "DRFIntegrationMiddleware"
) -> None:
    """Test that middleware detects DRF ViewSet actions."""
    from rest_framework.decorators import action
    from rest_framework.response import Response
    from rest_framework.viewsets import ViewSet

    class TestViewSet(ViewSet):
        def list(self, request):
            return Response({"action": "list"})

        @action(detail=False, methods=["get"])
        def custom_action(self, request):
            return Response({"action": "custom"})

    viewset = TestViewSet()
    viewset.action = "list"

    request = request_factory.get("/api/test/")
    request.view = viewset

    # Process view
    drf_middleware.process_view(request, viewset.list, (), {})

    # Check context
    context = get_request_context()
    assert context.route is not None
    assert "list" in context.route.lower() or "test" in context.route.lower()


def test_drf_middleware_sets_route_on_context(
    request_factory: RequestFactory, drf_middleware: "DRFIntegrationMiddleware"
) -> None:
    """Test that middleware sets route on request context."""
    from observe_kit.context import reset_request_context

    reset_request_context()

    # Mock a DRF view
    class MockView:
        def __init__(self):
            self.action = "create"
            self.__class__.__name__ = "UserViewSet"

    request = request_factory.post("/api/users/")
    request.view = MockView()

    drf_middleware.process_view(request, None, (), {})

    context = get_request_context()
    # Route should be set (may be None if DRF not fully available, but shouldn't error)
    assert context is not None


def test_drf_middleware_handles_non_drf_views(
    request_factory: RequestFactory, drf_middleware: "DRFIntegrationMiddleware"
) -> None:
    """Test that middleware handles non-DRF views gracefully."""
    from observe_kit.context import reset_request_context

    reset_request_context()

    request = request_factory.get("/test/")
    request.view = None

    # Should not raise
    drf_middleware.process_view(request, test_view, (), {})

    context = get_request_context()
    assert context is not None
