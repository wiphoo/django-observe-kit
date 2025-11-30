"""Tests for Wagtail Sentry breadcrumbs."""

import sys
from unittest.mock import Mock, patch

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_add_wagtail_breadcrumb_with_sentry() -> None:
    """Test adding breadcrumb when Sentry is installed."""
    from observe_kit.wagtail_integration import sentry_breadcrumbs

    mock_sentry = Mock()
    mock_sentry.add_breadcrumb = Mock()

    with patch("importlib.util.find_spec", return_value=Mock()):
        original_sentry = sys.modules.get("sentry_sdk")
        sys.modules["sentry_sdk"] = mock_sentry
        try:
            import importlib

            importlib.reload(sentry_breadcrumbs)
            sentry_breadcrumbs.add_wagtail_breadcrumb("test_category", "test_message")

            mock_sentry.add_breadcrumb.assert_called_once_with(
                category="test_category", message="test_message"
            )
        finally:
            if original_sentry:
                sys.modules["sentry_sdk"] = original_sentry
            elif "sentry_sdk" in sys.modules:
                del sys.modules["sentry_sdk"]


def test_add_wagtail_breadcrumb_without_sentry() -> None:
    """Test adding breadcrumb when Sentry is not installed."""
    from observe_kit.wagtail_integration import sentry_breadcrumbs

    with patch("importlib.util.find_spec", return_value=None):
        original_sentry = sys.modules.get("sentry_sdk")
        if "sentry_sdk" in sys.modules:
            del sys.modules["sentry_sdk"]
        try:
            import importlib

            importlib.reload(sentry_breadcrumbs)
            # Should not raise an error
            sentry_breadcrumbs.add_wagtail_breadcrumb("test_category", "test_message")
        finally:
            if original_sentry:
                sys.modules["sentry_sdk"] = original_sentry
