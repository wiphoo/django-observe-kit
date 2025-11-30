"""Unit tests for DRF exception handler."""

from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory


@pytest.fixture
def request_factory() -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


def test_observed_exception_handler_returns_4xx_response() -> None:
    """Test that handler returns 4xx responses without modification."""
    from rest_framework import status  # noqa: F401
    from rest_framework.response import Response  # noqa: F401

    from observe_kit.drf.exception_handler import observed_exception_handler  # noqa: F401

    exc = ValueError("Test error")
    context = {"request": None}
    mock_response = Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    with patch("observe_kit.drf.exception_handler.exception_handler", return_value=mock_response):
        result = observed_exception_handler(exc, context)

        assert result == mock_response


def test_observed_exception_handler_creates_500_response() -> None:
    """Test that handler creates 500 response when exception_handler returns None."""
    from rest_framework import status  # noqa: F401

    from observe_kit.drf.exception_handler import observed_exception_handler  # noqa: F401

    exc = ValueError("Test error")
    context = {"request": None}

    with patch("observe_kit.drf.exception_handler.exception_handler", return_value=None):
        result = observed_exception_handler(exc, context)

        assert result.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert result.data == {"detail": "Server Error"}


def test_observed_exception_handler_captures_sentry_exception() -> None:
    """Test that handler captures exception in Sentry when available."""
    import importlib.util
    import sys

    from observe_kit.drf.exception_handler import observed_exception_handler  # noqa: F401

    exc = ValueError("Test error")
    context = {"request": None}

    mock_sentry = Mock()
    mock_sentry.capture_exception = Mock()

    with patch("observe_kit.drf.exception_handler.exception_handler", return_value=None):
        with patch.object(importlib.util, "find_spec", return_value=Mock()):
            original_sentry = sys.modules.get("sentry_sdk")
            sys.modules["sentry_sdk"] = mock_sentry
            try:
                import importlib

                import observe_kit.drf.exception_handler

                importlib.reload(observe_kit.drf.exception_handler)
                observed_exception_handler(exc, context)

                mock_sentry.capture_exception.assert_called_once_with(exc)
            finally:
                if original_sentry:
                    sys.modules["sentry_sdk"] = original_sentry
                elif "sentry_sdk" in sys.modules:
                    del sys.modules["sentry_sdk"]


def test_observed_exception_handler_logs_request_complete() -> None:
    """Test that handler logs request completion."""
    from observe_kit.drf.exception_handler import observed_exception_handler  # noqa: F401

    exc = ValueError("Test error")
    factory = RequestFactory()
    request = factory.get("/test/")
    context = {"request": request}

    with patch("observe_kit.drf.exception_handler.exception_handler", return_value=None):
        with patch("observe_kit.drf.exception_handler.log_request_complete") as mock_log:
            observed_exception_handler(exc, context)

            mock_log.assert_called_once()
            # Check that the exception message is in the call arguments
            call_args_str = str(mock_log.call_args)
            assert "Test error" in call_args_str or "ValueError" in call_args_str


def test_observed_exception_handler_without_sentry() -> None:
    """Test that handler works when Sentry is not installed."""
    from rest_framework import status  # noqa: F401

    from observe_kit.drf.exception_handler import observed_exception_handler  # noqa: F401

    exc = ValueError("Test error")
    context = {"request": None}

    with patch("observe_kit.drf.exception_handler.exception_handler", return_value=None):
        with patch("observe_kit.drf.exception_handler.importlib.util.find_spec", return_value=None):
            result = observed_exception_handler(exc, context)

            assert result.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
