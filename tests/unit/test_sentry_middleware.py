"""Unit tests for Sentry middleware."""

from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory

from observe_kit.context import RequestContext, reset_request_context, set_request_context
from observe_kit.sentry.middleware import SentryContextMiddleware


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


def test_sentry_context_middleware_with_sentry_sdk(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that SentryContextMiddleware sets tags when sentry_sdk is available."""
    import importlib.util
    import sys

    middleware = SentryContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    context = RequestContext()
    context.trace_id = "test-trace-123"
    context.tenant_id = "tenant-456"
    context.method = "GET"
    context.path = "/test/"
    set_request_context(context)

    mock_sentry = Mock()
    mock_scope = Mock()
    mock_sentry.get_isolation_scope.return_value = mock_scope

    with patch.object(importlib.util, "find_spec", return_value=Mock()):
        # Patch sentry_sdk in sys.modules before the import happens
        original_sentry = sys.modules.get("sentry_sdk")
        sys.modules["sentry_sdk"] = mock_sentry
        try:
            # Reload the module to use the mocked sentry_sdk
            import importlib

            import observe_kit.sentry.middleware

            importlib.reload(observe_kit.sentry.middleware)
            middleware.process_request(request)

            assert mock_scope.set_tag.call_count == 4
            mock_scope.set_tag.assert_any_call("otel.trace_id", "test-trace-123")
            mock_scope.set_tag.assert_any_call("tenant_id", "tenant-456")
            mock_scope.set_tag.assert_any_call("http.method", "GET")
            mock_scope.set_tag.assert_any_call("http.path", "/test/")
        finally:
            if original_sentry:
                sys.modules["sentry_sdk"] = original_sentry
            elif "sentry_sdk" in sys.modules:
                del sys.modules["sentry_sdk"]


def test_sentry_context_middleware_without_sentry_sdk(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that SentryContextMiddleware returns early when sentry_sdk is not available."""
    middleware = SentryContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    with patch("observe_kit.sentry.middleware.importlib.util.find_spec", return_value=None):
        result = middleware.process_request(request)

        assert result is None


def test_sentry_context_middleware_handles_exception(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that SentryContextMiddleware handles exceptions gracefully."""
    import importlib.util
    import logging
    import sys

    middleware = SentryContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    mock_sentry = Mock()
    mock_sentry.get_isolation_scope.side_effect = Exception("Test")

    with patch.object(importlib.util, "find_spec", return_value=Mock()):
        original_sentry = sys.modules.get("sentry_sdk")
        sys.modules["sentry_sdk"] = mock_sentry
        try:
            import importlib

            import observe_kit.sentry.middleware

            importlib.reload(observe_kit.sentry.middleware)
            with patch.object(
                logging.getLogger("observe_kit.sentry.middleware"), "warning"
            ) as mock_logger:
                result = middleware.process_request(request)

                mock_logger.assert_called_once()
                assert result is None
        finally:
            if original_sentry:
                sys.modules["sentry_sdk"] = original_sentry
            elif "sentry_sdk" in sys.modules:
                del sys.modules["sentry_sdk"]


def test_sentry_context_middleware_partial_context(
    request_factory: RequestFactory, mock_get_response: Mock, reset_context: None
) -> None:
    """Test that SentryContextMiddleware only sets tags for available context fields."""
    import importlib.util
    import sys

    middleware = SentryContextMiddleware(mock_get_response)
    request = request_factory.get("/test/")

    context = RequestContext()
    context.trace_id = "test-trace-123"
    # tenant_id, method, path are None
    set_request_context(context)

    mock_sentry = Mock()
    mock_scope = Mock()
    mock_sentry.get_isolation_scope.return_value = mock_scope

    with patch.object(importlib.util, "find_spec", return_value=Mock()):
        original_sentry = sys.modules.get("sentry_sdk")
        sys.modules["sentry_sdk"] = mock_sentry
        try:
            import importlib

            import observe_kit.sentry.middleware

            importlib.reload(observe_kit.sentry.middleware)
            middleware.process_request(request)

            # Only trace_id should be set
            assert mock_scope.set_tag.call_count == 1
            mock_scope.set_tag.assert_called_once_with("otel.trace_id", "test-trace-123")
        finally:
            if original_sentry:
                sys.modules["sentry_sdk"] = original_sentry
            elif "sentry_sdk" in sys.modules:
                del sys.modules["sentry_sdk"]
