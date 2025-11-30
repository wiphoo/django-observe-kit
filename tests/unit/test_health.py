"""Unit tests for health check endpoints."""

import json
from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory

from observe_kit.health import (
    _check_database,
    _check_otel,
    _check_sentry,
    healthz,
    healthz_detailed,
)


@pytest.fixture
def request_factory() -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


def test_healthz_simple(request_factory: RequestFactory) -> None:
    """Test simple health check returns 'ok'."""
    request = request_factory.get("/healthz/")
    response = healthz(request, detailed=False)

    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Content-Type"] == "text/plain"


def test_healthz_detailed_all_healthy(request_factory: RequestFactory) -> None:
    """Test detailed health check when all components are healthy."""
    request = request_factory.get("/healthz/")

    with patch(
        "observe_kit.health._check_database", return_value={"status": "healthy", "error": None}
    ):
        with patch(
            "observe_kit.health._check_otel", return_value={"status": "healthy", "error": None}
        ):
            with patch(
                "observe_kit.health._check_sentry",
                return_value={"status": "healthy", "error": None},
            ):
                response = healthz(request, detailed=True)

                assert response.status_code == 200
                data = json.loads(response.content)
                assert data["status"] == "healthy"
                assert "components" in data
                assert data["components"]["database"]["status"] == "healthy"
                assert data["components"]["otel"]["status"] == "healthy"
                assert data["components"]["sentry"]["status"] == "healthy"


def test_healthz_detailed_with_unhealthy(request_factory: RequestFactory) -> None:
    """Test detailed health check when a component is unhealthy."""
    request = request_factory.get("/healthz/")

    with patch(
        "observe_kit.health._check_database",
        return_value={"status": "unhealthy", "error": "Connection failed"},
    ):
        with patch(
            "observe_kit.health._check_otel", return_value={"status": "healthy", "error": None}
        ):
            with patch(
                "observe_kit.health._check_sentry",
                return_value={"status": "healthy", "error": None},
            ):
                response = healthz(request, detailed=True)

                assert response.status_code == 503
                data = json.loads(response.content)
                assert data["status"] == "unhealthy"
                assert data["components"]["database"]["status"] == "unhealthy"
                assert data["components"]["database"]["error"] == "Connection failed"


def test_healthz_detailed_with_not_configured(request_factory: RequestFactory) -> None:
    """Test detailed health check when components are not configured (should still be healthy)."""
    request = request_factory.get("/healthz/")

    with patch(
        "observe_kit.health._check_database", return_value={"status": "healthy", "error": None}
    ):
        with patch(
            "observe_kit.health._check_otel",
            return_value={"status": "not_configured", "error": "Not configured"},
        ):
            with patch(
                "observe_kit.health._check_sentry",
                return_value={"status": "not_configured", "error": "Not installed"},
            ):
                response = healthz(request, detailed=True)

                assert response.status_code == 200
                data = json.loads(response.content)
                assert data["status"] == "healthy"
                assert data["components"]["otel"]["status"] == "not_configured"
                assert data["components"]["sentry"]["status"] == "not_configured"


def test_healthz_detailed_function(request_factory: RequestFactory) -> None:
    """Test healthz_detailed convenience function."""
    request = request_factory.get("/healthz/detailed/")

    with patch(
        "observe_kit.health._check_database", return_value={"status": "healthy", "error": None}
    ):
        with patch(
            "observe_kit.health._check_otel", return_value={"status": "healthy", "error": None}
        ):
            with patch(
                "observe_kit.health._check_sentry",
                return_value={"status": "healthy", "error": None},
            ):
                response = healthz_detailed(request)

                assert response.status_code == 200
                data = json.loads(response.content)
                assert data["status"] == "healthy"


def test_check_database_success() -> None:
    """Test database health check when database is accessible."""
    with patch("observe_kit.health.connection") as mock_connection:
        mock_cursor = Mock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.execute.return_value = None

        result = _check_database()

        assert result["status"] == "healthy"
        assert result["error"] is None
        mock_cursor.execute.assert_called_once_with("SELECT 1")


def test_check_database_failure() -> None:
    """Test database health check when database connection fails."""
    with patch("observe_kit.health.connection") as mock_connection:
        mock_connection.cursor.side_effect = Exception("Connection refused")

        with patch("observe_kit.health.logger") as mock_logger:
            result = _check_database()

            assert result["status"] == "unhealthy"
            assert result["error"] == "Connection refused"
            mock_logger.warning.assert_called_once()


def test_check_otel_healthy() -> None:
    """Test OTEL health check when tracer provider is configured."""
    mock_provider = Mock()
    mock_provider._span_processors = [Mock()]

    mock_trace = Mock()
    mock_trace.get_tracer_provider.return_value = mock_provider

    with patch("opentelemetry.trace", mock_trace):
        result = _check_otel()

        assert result["status"] == "healthy"
        assert result["error"] is None


def test_check_otel_not_configured_no_provider() -> None:
    """Test OTEL health check when tracer provider is None."""
    mock_trace = Mock()
    mock_trace.get_tracer_provider.return_value = None

    with patch("opentelemetry.trace", mock_trace):
        result = _check_otel()

        assert result["status"] == "not_configured"
        assert "not initialized" in result["error"]


def test_check_otel_not_configured_no_processors() -> None:
    """Test OTEL health check when no span processors are configured."""
    mock_provider = Mock()
    mock_provider._span_processors = []

    mock_trace = Mock()
    mock_trace.get_tracer_provider.return_value = mock_provider

    with patch("opentelemetry.trace", mock_trace):
        result = _check_otel()

        assert result["status"] == "not_configured"
        assert "No span processors" in result["error"]


def test_check_otel_exception() -> None:
    """Test OTEL health check when an exception occurs."""
    # Make get_tracer_provider raise an exception
    mock_trace = Mock()
    mock_trace.get_tracer_provider.side_effect = Exception("Test error")

    with patch("opentelemetry.trace", mock_trace):
        with patch("observe_kit.health.logger") as mock_logger:
            result = _check_otel()

            assert result["status"] == "unhealthy"
            assert "Test error" in result["error"]
            mock_logger.warning.assert_called_once()


def test_check_sentry_not_installed() -> None:
    """Test Sentry health check when sentry_sdk is not installed."""
    with patch("observe_kit.health.importlib.util.find_spec", return_value=None):
        result = _check_sentry()

        assert result["status"] == "not_configured"
        assert "not installed" in result["error"]


def test_check_sentry_not_configured_no_client() -> None:
    """Test Sentry health check when Sentry client is not initialized."""
    mock_hub = Mock()
    mock_hub.current.client = None

    mock_sentry = Mock()
    mock_sentry.Hub = mock_hub

    with patch("observe_kit.health.importlib.util.find_spec", return_value=Mock()):
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *args, **kwargs: mock_sentry
            if name == "sentry_sdk"
            else __import__(name, *args, **kwargs),
        ):
            result = _check_sentry()

            assert result["status"] == "not_configured"
            assert "not initialized" in result["error"]


def test_check_sentry_not_configured_no_dsn() -> None:
    """Test Sentry health check when DSN is not configured."""
    mock_client = Mock()
    mock_client.dsn = None
    mock_hub = Mock()
    mock_hub.current.client = mock_client

    mock_sentry = Mock()
    mock_sentry.Hub = mock_hub

    with patch("observe_kit.health.importlib.util.find_spec", return_value=Mock()):
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *args, **kwargs: mock_sentry
            if name == "sentry_sdk"
            else __import__(name, *args, **kwargs),
        ):
            result = _check_sentry()

            assert result["status"] == "not_configured"
            assert "DSN not configured" in result["error"]


def test_check_sentry_healthy() -> None:
    """Test Sentry health check when Sentry is properly configured."""
    mock_client = Mock()
    mock_client.dsn = "https://example@sentry.io/123"
    mock_hub = Mock()
    mock_hub.current.client = mock_client

    mock_sentry = Mock()
    mock_sentry.Hub = mock_hub

    with patch("observe_kit.health.importlib.util.find_spec", return_value=Mock()):
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *args, **kwargs: mock_sentry
            if name == "sentry_sdk"
            else __import__(name, *args, **kwargs),
        ):
            result = _check_sentry()

            assert result["status"] == "healthy"
            assert result["error"] is None


def test_check_sentry_exception() -> None:
    """Test Sentry health check when an exception occurs."""
    with patch("observe_kit.health.importlib.util.find_spec", side_effect=Exception("Test error")):
        with patch("observe_kit.health.logger") as mock_logger:
            result = _check_sentry()

            assert result["status"] == "unhealthy"
            assert result["error"] == "Test error"
            mock_logger.warning.assert_called_once()
