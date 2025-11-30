"""Integration tests for health check endpoints with real Django requests."""

import pytest
from django.test import Client

pytestmark = pytest.mark.integration


def test_healthz_endpoint_returns_ok(django_client: Client) -> None:
    """Test that /healthz endpoint returns 200 OK."""
    response = django_client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_healthz_detailed_endpoint_returns_json(django_client: Client) -> None:
    """Test that /healthz/detailed endpoint returns JSON."""
    response = django_client.get("/healthz/detailed")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"

    data = response.json()
    assert "status" in data
    assert "components" in data
    assert isinstance(data["components"], dict)


def test_healthz_detailed_includes_database_status(django_client: Client) -> None:
    """Test that detailed health check includes database status."""
    response = django_client.get("/healthz/detailed")
    data = response.json()

    assert "database" in data["components"]
    db_status = data["components"]["database"]
    assert "status" in db_status
    assert db_status["status"] in ("healthy", "unhealthy", "not_configured")


def test_healthz_detailed_includes_otel_status(django_client: Client) -> None:
    """Test that detailed health check includes OTEL status."""
    response = django_client.get("/healthz/detailed")
    data = response.json()

    assert "otel" in data["components"]
    otel_status = data["components"]["otel"]
    assert "status" in otel_status


def test_healthz_detailed_includes_sentry_status(django_client: Client) -> None:
    """Test that detailed health check includes Sentry status."""
    response = django_client.get("/healthz/detailed")
    data = response.json()

    assert "sentry" in data["components"]
    sentry_status = data["components"]["sentry"]
    assert "status" in sentry_status
