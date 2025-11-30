"""Integration tests for DRF middleware with real HTTP requests.

These tests verify the DRF integration middleware correctly:
1. Detects ViewSet actions
2. Sets route context for metrics/tracing
3. Works with the full middleware stack
"""

from typing import TYPE_CHECKING

import pytest
from django.http import JsonResponse
from django.test import Client, RequestFactory

pytestmark = pytest.mark.integration

# Lazy imports to avoid Django configuration issues at module load time
if TYPE_CHECKING:
    from rest_framework.viewsets import ViewSet


from observe_kit.context import get_request_context, reset_request_context  # noqa: E402
from observe_kit.drf.integration import DRFIntegrationMiddleware, detect_drf_route  # noqa: E402


def _create_test_viewset_class():
    """Create a test ViewSet class (deferred to avoid early Django init)."""
    from rest_framework import status
    from rest_framework.decorators import action
    from rest_framework.response import Response
    from rest_framework.viewsets import ViewSet

    class TestUserViewSet(ViewSet):
        """ViewSet for testing DRF integration."""

        def list(self, request):
            """List action."""
            return Response({"users": [], "action": "list"})

        def retrieve(self, request, pk=None):
            """Retrieve action."""
            return Response({"user": {"id": pk}, "action": "retrieve"})

        def create(self, request):
            """Create action."""
            return Response(
                {"user": {"id": 1}, "action": "create"}, status=status.HTTP_201_CREATED
            )

        @action(detail=False, methods=["get"])
        def active(self, request):
            """Custom action: list active users."""
            return Response({"users": [], "action": "active"})

        @action(detail=True, methods=["post"])
        def deactivate(self, request, pk=None):
            """Custom action: deactivate a user."""
            return Response({"user": {"id": pk, "active": False}, "action": "deactivate"})

    return TestUserViewSet


@pytest.fixture
def api_client(django_client: Client):
    """DRF API test client."""
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def request_factory(django_client: Client) -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


@pytest.fixture
def drf_middleware(django_client: Client) -> DRFIntegrationMiddleware:
    """DRF integration middleware instance."""

    def get_response(request):
        return JsonResponse({"status": "ok"})

    return DRFIntegrationMiddleware(get_response)


@pytest.fixture
def test_viewset_class(django_client: Client):
    """Get the test ViewSet class."""
    return _create_test_viewset_class()


class TestDRFRouteDetection:
    """Tests for DRF route detection logic."""

    def test_detect_viewset_list_action(
        self,
        request_factory: RequestFactory,
        drf_middleware: DRFIntegrationMiddleware,
        test_viewset_class,
    ) -> None:
        """Test detection of ViewSet list action."""
        reset_request_context()

        # Create request with ViewSet view attached
        request = request_factory.get("/api/users/")
        viewset = test_viewset_class()
        viewset.action = "list"
        viewset.request = request
        viewset.kwargs = {}
        request.view = viewset

        # Process through middleware
        drf_middleware.process_view(request, viewset.list, (), {})

        # Verify context was set
        context = get_request_context()
        assert context.route is not None
        assert "TestUserViewSet" in context.route
        assert "list" in context.route

    def test_detect_viewset_retrieve_action(
        self,
        request_factory: RequestFactory,
        drf_middleware: DRFIntegrationMiddleware,
        test_viewset_class,
    ) -> None:
        """Test detection of ViewSet retrieve action."""
        reset_request_context()

        request = request_factory.get("/api/users/123/")
        viewset = test_viewset_class()
        viewset.action = "retrieve"
        viewset.request = request
        viewset.kwargs = {"pk": "123"}
        request.view = viewset

        drf_middleware.process_view(request, viewset.retrieve, (), {"pk": "123"})

        context = get_request_context()
        assert context.route is not None
        assert "retrieve" in context.route

    def test_detect_custom_action(
        self,
        request_factory: RequestFactory,
        drf_middleware: DRFIntegrationMiddleware,
        test_viewset_class,
    ) -> None:
        """Test detection of custom ViewSet action."""
        reset_request_context()

        request = request_factory.get("/api/users/active/")
        viewset = test_viewset_class()
        viewset.action = "active"
        viewset.request = request
        viewset.kwargs = {}
        request.view = viewset

        drf_middleware.process_view(request, viewset.active, (), {})

        context = get_request_context()
        assert context.route is not None
        assert "active" in context.route

    def test_handles_non_drf_view_gracefully(
        self, request_factory: RequestFactory, drf_middleware: DRFIntegrationMiddleware
    ) -> None:
        """Test that non-DRF views don't cause errors."""
        reset_request_context()

        def plain_view(request):
            return JsonResponse({"status": "ok"})

        request = request_factory.get("/plain/")
        request.view = None

        # Should not raise
        drf_middleware.process_view(request, plain_view, (), {})

        # Context should still be valid (just no DRF route)
        context = get_request_context()
        assert context is not None


class TestDetectDRFRouteFunction:
    """Tests for the detect_drf_route() function."""

    def test_returns_none_for_non_drf_request(
        self, request_factory: RequestFactory, django_client: Client
    ) -> None:
        """Test that non-DRF requests return None."""
        request = request_factory.get("/plain/")
        request.view = None

        result = detect_drf_route(request)
        assert result is None

    def test_returns_route_for_viewset_with_action(
        self, request_factory: RequestFactory, test_viewset_class
    ) -> None:
        """Test that ViewSet with action returns formatted route."""
        request = request_factory.post("/api/users/")
        viewset = test_viewset_class()
        viewset.action = "create"
        request.view = viewset

        result = detect_drf_route(request)
        assert result is not None
        assert "drf.TestUserViewSet.create" == result

    def test_returns_route_with_correct_format(
        self, request_factory: RequestFactory, test_viewset_class
    ) -> None:
        """Test that route follows 'drf.<ViewSet>.<action>' format."""
        request = request_factory.get("/api/users/")
        viewset = test_viewset_class()
        viewset.action = "list"
        request.view = viewset

        result = detect_drf_route(request)
        assert result == "drf.TestUserViewSet.list"


class TestMiddlewareIntegration:
    """Tests for full middleware integration."""

    def test_middleware_sets_context_for_metrics(
        self,
        request_factory: RequestFactory,
        drf_middleware: DRFIntegrationMiddleware,
        test_viewset_class,
    ) -> None:
        """Test that middleware sets context that can be used by metrics."""
        reset_request_context()

        request = request_factory.delete("/api/users/456/")
        viewset = test_viewset_class()
        viewset.action = "destroy"
        viewset.request = request
        viewset.kwargs = {"pk": "456"}
        request.view = viewset

        # Create a mock span to verify span naming
        class MockSpan:
            def __init__(self):
                self.updated_name = None

            def update_name(self, name: str) -> None:
                self.updated_name = name

        request._observe_kit_span = MockSpan()

        drf_middleware.process_view(request, lambda r: None, (), {"pk": "456"})

        # Verify context route is set
        context = get_request_context()
        assert context.route is not None

        # Verify span was updated
        assert request._observe_kit_span.updated_name is not None
        assert "destroy" in request._observe_kit_span.updated_name

    def test_middleware_chain_preserves_context(
        self, request_factory: RequestFactory, test_viewset_class
    ) -> None:
        """Test that context is preserved through middleware chain."""
        reset_request_context()

        responses = []

        def get_response(request):
            # Capture context at response time
            ctx = get_request_context()
            responses.append({"route": ctx.route})
            return JsonResponse({"status": "ok"})

        middleware = DRFIntegrationMiddleware(get_response)

        request = request_factory.get("/api/users/")
        viewset = test_viewset_class()
        viewset.action = "list"
        request.view = viewset

        # Process view first (sets context)
        middleware.process_view(request, viewset.list, (), {})

        # Then call the middleware (which calls get_response)
        middleware(request)

        # Context should have been available in get_response
        assert len(responses) == 1
        assert responses[0]["route"] is not None


class TestErrorHandling:
    """Tests for error handling in DRF middleware."""

    def test_handles_missing_action_attribute(
        self, request_factory: RequestFactory, drf_middleware: DRFIntegrationMiddleware
    ) -> None:
        """Test handling when ViewSet has no action attribute."""
        from rest_framework.viewsets import ViewSet

        reset_request_context()

        request = request_factory.get("/api/test/")

        class IncompleteViewSet(ViewSet):
            pass

        viewset = IncompleteViewSet()
        # Don't set action attribute
        request.view = viewset

        # Should not raise
        drf_middleware.process_view(request, lambda r: None, (), {})

        # Context should still be valid
        context = get_request_context()
        assert context is not None

    def test_handles_exception_in_route_detection(
        self, request_factory: RequestFactory, drf_middleware: DRFIntegrationMiddleware
    ) -> None:
        """Test that exceptions in route detection don't break the request."""
        reset_request_context()

        request = request_factory.get("/api/test/")

        # Create a view that raises when accessing action
        class BrokenViewSet:
            @property
            def action(self):
                raise RuntimeError("Simulated error")

            @property
            def __class__(self):
                return type("BrokenViewSet", (), {})

        request.view = BrokenViewSet()

        # Should not raise (middleware catches exceptions)
        drf_middleware.process_view(request, lambda r: None, (), {})

        # Request should continue
        context = get_request_context()
        assert context is not None
