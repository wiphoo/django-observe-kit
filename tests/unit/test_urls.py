"""Tests for URL patterns."""

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_urls_importable() -> None:
    """Test that URLs module can be imported."""
    from observe_kit.urls import urlpatterns

    assert urlpatterns is not None
    assert len(urlpatterns) == 1


def test_urls_contains_metrics() -> None:
    """Test that URLs contain metrics endpoint."""
    from observe_kit.urls import urlpatterns

    assert len(urlpatterns) == 1
    assert urlpatterns[0].pattern._route == "metrics"
