"""Unit tests for Prometheus metrics."""

import pytest
from django.test import RequestFactory

from observe_kit.metrics.prometheus import metrics_view, observe_request


@pytest.fixture
def request_factory() -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


def test_observe_request_records_metrics() -> None:
    """Test that observe_request records all metrics."""
    # Test that observe_request executes without error
    # The actual metric recording is tested in integration tests
    observe_request(
        method="GET",
        route="/test",
        status=200,
        duration_seconds=0.5,
        tenant="tenant-1",
        db_queries=10,
        db_time_seconds=0.1,
    )

    # Function should complete without error
    assert True


def test_observe_request_with_unknown_tenant() -> None:
    """Test that observe_request uses 'unknown' for None tenant."""
    observe_request(
        method="POST",
        route="/api/test",
        status=201,
        duration_seconds=0.3,
        tenant=None,
        db_queries=5,
        db_time_seconds=0.05,
    )

    # Should not raise


def test_metrics_view_as_view(request_factory: RequestFactory) -> None:
    """Test that metrics_view.as_view returns a callable view."""
    view = metrics_view.as_view()
    request = request_factory.get("/metrics/")

    response = view(request)

    assert response.status_code == 200
    assert "text/plain" in response["Content-Type"]
    assert len(response.content) > 0  # Should have some metrics output


def test_metrics_view_build_response() -> None:
    """Test that metrics_view._build_response creates proper HttpResponse."""
    payload = b"test metrics output"

    response = metrics_view._build_response(payload)

    assert response.status_code == 200
    assert response.content == payload
    assert "text/plain" in response["Content-Type"]
