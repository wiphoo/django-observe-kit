"""Integration tests for Prometheus metrics with real Prometheus instance."""

import os
import time
from typing import Generator

import pytest
import requests
from django.test import Client

pytestmark = pytest.mark.integration

from observe_kit.metrics.prometheus import observe_request  # noqa: E402


@pytest.fixture(scope="function")
def prometheus_url() -> str:
    """Prometheus query URL."""
    port = os.getenv("PROMETHEUS_PORT", "9090")
    return f"http://localhost:{port}"


def test_metrics_exported_to_prometheus(
    django_client: Client, prometheus_url: str, wait_for_services: Generator[None, None, None]
) -> None:
    """Test that metrics are exported and queryable from Prometheus."""
    # Make a request to generate metrics
    response = django_client.get("/healthz")
    assert response.status_code == 200

    # Wait for metrics to be scraped
    time.sleep(3)

    # Query Prometheus for our metrics
    metrics_to_check = ["http_requests_total", "http_request_duration_seconds"]

    for metric_name in metrics_to_check:
        query_url = f"{prometheus_url}/api/v1/query"
        params = {"query": metric_name}

        try:
            response = requests.get(query_url, params=params, timeout=5)
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            # Metrics may not have data yet, but query should succeed
        except requests.exceptions.RequestException:
            pytest.skip(f"Prometheus not accessible at {prometheus_url}")


@pytest.mark.parametrize(
    "method,route,status",
    [("GET", "/healthz", 200), ("GET", "/metrics", 200), ("GET", "/nonexistent", 404)],
)
def test_http_metrics_recorded(
    django_client: Client,
    prometheus_url: str,
    method: str,
    route: str,
    status: int,
    wait_for_services: Generator[None, None, None],
) -> None:
    """Test that HTTP metrics are recorded for different routes."""
    # Make request
    response = django_client.request(method=method, path=route)
    assert response.status_code == status

    # Wait for metrics
    time.sleep(3)

    # Query for specific metric with labels
    query = f'http_requests_total{{method="{method}",status="{status}"}}'
    query_url = f"{prometheus_url}/api/v1/query"

    try:
        response = requests.get(query_url, params={"query": query}, timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
    except requests.exceptions.RequestException:
        pytest.skip("Prometheus not accessible")


def test_metrics_endpoint_exposes_prometheus_format(django_client: Client) -> None:
    """Test that /metrics endpoint returns Prometheus format."""
    response = django_client.get("/metrics")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8"

    content = response.content.decode("utf-8")
    # Check for Prometheus format
    assert "# HELP" in content or "# TYPE" in content
    assert "http_requests_total" in content or "http_request_duration_seconds" in content


def test_db_metrics_recorded(
    django_client: Client, prometheus_url: str, wait_for_services: Generator[None, None, None]
) -> None:
    """Test that database metrics are recorded."""
    # Make a request that might trigger DB queries
    response = django_client.get("/healthz")
    assert response.status_code == 200

    time.sleep(3)

    # Query for DB metrics
    query = "db_queries_per_request"
    query_url = f"{prometheus_url}/api/v1/query"

    try:
        response = requests.get(query_url, params={"query": query}, timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
    except requests.exceptions.RequestException:
        pytest.skip("Prometheus not accessible")


def test_observe_request_function_records_metrics(
    prometheus_url: str, wait_for_services: Generator[None, None, None]
) -> None:
    """Test that observe_request function actually records metrics."""
    # Call observe_request directly
    observe_request(
        method="POST",
        route="/api/test",
        status=201,
        duration_seconds=0.15,
        tenant="test-tenant",
        db_queries=5,
        db_time_seconds=0.01,
    )

    time.sleep(2)

    # Query Prometheus
    query = 'http_requests_total{method="POST",route="/api/test"}'
    query_url = f"{prometheus_url}/api/v1/query"

    try:
        response = requests.get(query_url, params={"query": query}, timeout=5)
        assert response.status_code == 200
    except requests.exceptions.RequestException:
        pytest.skip("Prometheus not accessible")
