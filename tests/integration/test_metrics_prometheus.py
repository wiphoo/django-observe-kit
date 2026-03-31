"""Integration tests for Prometheus metrics with real Prometheus instance.

These tests verify:
1. Metrics are exported in Prometheus format by Django /metrics endpoint
2. Prometheus instance is running and accessible
3. Metrics values increment correctly when observe_request is called

Note: The Django test client doesn't run a real HTTP server, so Prometheus
cannot scrape metrics from the test client directly. These tests verify
the metrics are correctly recorded by checking the /metrics endpoint output.
"""

import os
import re
from typing import Generator, Optional

import pytest
import requests
from django.test import Client

pytestmark = pytest.mark.integration

from observe_kit.metrics.prometheus import observe_request  # noqa: E402


@pytest.fixture(scope="function")
def prometheus_url() -> str:
    """Prometheus query URL."""
    port = os.getenv(
        "OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT", "9090"
    )
    return f"http://localhost:{port}"


def get_metric_value_from_output(
    content: str, metric_name: str, labels: Optional[dict] = None
) -> Optional[float]:
    """Extract a metric value from Prometheus text format output.

    Args:
        content: Prometheus text format output
        metric_name: Name of the metric to find
        labels: Optional dict of label key-value pairs to match

    Returns:
        The metric value as float, or None if not found
    """
    # Build regex pattern for metric with optional labels
    if labels:
        # Labels can be in any order, so we need to be flexible
        # Match metric_name{...labels...} value
        for line in content.split("\n"):
            if line.startswith(metric_name + "{"):
                # Check if all required labels are present
                if all(f'{k}="{v}"' in line for k, v in labels.items()):
                    # Extract value at end of line
                    match = re.search(r"\}\s+(\d+\.?\d*(?:e[+-]?\d+)?)\s*$", line)
                    if match:
                        return float(match.group(1))
    else:
        # Simple metric without labels
        pattern = rf'^{re.escape(metric_name)}\s+(\d+\.?\d*(?:e[+-]?\d+)?)\s*$'
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            return float(match.group(1))

    return None


def test_prometheus_instance_is_running(
    prometheus_url: str, wait_for_prometheus: Generator[None, None, None]
) -> None:
    """Test that Prometheus instance is accessible."""
    # Check Prometheus health endpoint
    response = requests.get(f"{prometheus_url}/-/healthy", timeout=5)
    assert response.status_code == 200, "Prometheus health check failed"

    # Check we can query Prometheus
    query_url = f"{prometheus_url}/api/v1/query"
    response = requests.get(query_url, params={"query": "up"}, timeout=5)
    assert response.status_code == 200, "Prometheus query endpoint failed"

    data = response.json()
    assert data["status"] == "success", f"Prometheus query error: {data}"


def test_metrics_endpoint_exposes_prometheus_format(
    django_client: Client, wait_for_prometheus: Generator[None, None, None]
) -> None:
    """Test that /metrics endpoint returns valid Prometheus format."""
    response = django_client.get("/metrics")
    assert response.status_code == 200

    # Check Content-Type
    content_type = response["Content-Type"]
    assert content_type.startswith("text/plain; version=")
    assert "charset=utf-8" in content_type

    # Check Prometheus format markers
    content = response.content.decode("utf-8")
    assert "# HELP" in content or "# TYPE" in content, "Missing Prometheus format markers"

    # Verify key metrics are present
    expected_metrics = [
        "http_requests_total",
        "http_request_duration_seconds",
    ]
    for metric in expected_metrics:
        assert metric in content, f"Expected metric '{metric}' not found in /metrics output"


def test_http_request_counter_increments_in_metrics_output(
    django_client: Client,
    wait_for_prometheus: Generator[None, None, None],
) -> None:
    """Test that HTTP requests increment the counter in /metrics output."""
    # Get initial state
    response = django_client.get("/metrics")
    assert response.status_code == 200
    initial_content = response.content.decode("utf-8")

    # Get initial value (might be None if no requests yet)
    initial_value = get_metric_value_from_output(
        initial_content, "http_requests_total", {"method": "GET", "status": "200"}
    )
    initial_count = initial_value or 0

    # Make additional requests to generate metrics
    for _ in range(3):
        response = django_client.get("/metrics")
        assert response.status_code == 200

    # Get new metrics output
    response = django_client.get("/metrics")
    new_content = response.content.decode("utf-8")

    new_value = get_metric_value_from_output(
        new_content, "http_requests_total", {"method": "GET", "status": "200"}
    )

    assert new_value is not None, "http_requests_total metric not found"
    # We made at least 4 requests (1 initial + 3 + 1 final)
    assert new_value >= initial_count + 4, (
        f"Expected counter to increment by at least 4, was {initial_count}, now {new_value}"
    )


def test_http_request_duration_histogram_in_metrics_output(
    django_client: Client,
    wait_for_prometheus: Generator[None, None, None],
) -> None:
    """Test that HTTP request duration histogram is recorded in /metrics output."""
    # Make a request
    response = django_client.get("/metrics")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Histogram should have _bucket, _sum, and _count metrics
    assert "http_request_duration_seconds_bucket" in content, (
        "Duration histogram buckets not found"
    )
    assert "http_request_duration_seconds_sum" in content, (
        "Duration histogram sum not found"
    )
    assert "http_request_duration_seconds_count" in content, (
        "Duration histogram count not found"
    )


def test_observe_request_records_metrics(
    django_client: Client,
    wait_for_prometheus: Generator[None, None, None],
) -> None:
    """Test that observe_request() function records metrics visible in /metrics."""
    # Use unique labels to identify our test
    test_route = "/api/observe-test-route"
    test_tenant = "observe-test-tenant"

    # Record metrics directly
    observe_request(
        method="POST",
        route=test_route,
        status=201,
        duration_seconds=0.123,
        tenant=test_tenant,
        db_queries=3,
        db_time_seconds=0.015,
    )

    # Check /metrics output contains our metric
    response = django_client.get("/metrics")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Look for our specific metric
    assert f'route="{test_route}"' in content, (
        f"Route label '{test_route}' not found in metrics output"
    )
    assert f'tenant="{test_tenant}"' in content, (
        f"Tenant label '{test_tenant}' not found in metrics output"
    )

    # Verify the counter value
    value = get_metric_value_from_output(
        content,
        "http_requests_total",
        {"method": "POST", "route": test_route, "status": "201", "tenant": test_tenant}
    )
    assert value is not None, "Metric not found with expected labels"
    assert value >= 1, f"Expected counter >= 1, got {value}"


def test_db_metrics_recorded(
    django_client: Client,
    wait_for_prometheus: Generator[None, None, None],
) -> None:
    """Test that database metrics are recorded in /metrics output."""
    # Record a request with DB metrics
    observe_request(
        method="GET",
        route="/api/db-test",
        status=200,
        duration_seconds=0.1,
        tenant="db-test-tenant",
        db_queries=10,
        db_time_seconds=0.05,
    )

    response = django_client.get("/metrics")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Check DB metrics exist
    assert "db_queries_per_request" in content, "DB queries metric not found"
    assert "db_time_per_request_seconds" in content, "DB time metric not found"


def test_metrics_with_404_status(
    django_client: Client,
    wait_for_prometheus: Generator[None, None, None],
) -> None:
    """Test that metrics are recorded for 404 responses."""
    # Make a request to a non-existent path
    response = django_client.get("/nonexistent-path-for-404-test")
    assert response.status_code == 404

    # Get metrics
    response = django_client.get("/metrics")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Check 404 status is recorded
    assert 'status="404"' in content, "404 status not found in metrics"


def test_prometheus_can_be_queried(
    prometheus_url: str, wait_for_prometheus: Generator[None, None, None]
) -> None:
    """Test that Prometheus API can be queried successfully."""
    query_url = f"{prometheus_url}/api/v1/query"

    # Query for 'up' metric which always exists
    response = requests.get(query_url, params={"query": "up"}, timeout=5)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success", f"Query failed: {data}"

    # 'up' metric should always exist and have results
    assert data["data"]["result"], "'up' metric should exist and have scrape targets"
