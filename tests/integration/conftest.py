"""Shared fixtures for integration tests.

Integration Test Categories:
============================

1. **External Service Tests** (require Docker)
   - Use fixtures: wait_for_prometheus, wait_for_otel_collector, wait_for_hyperdx
   - Run with: make integration-up && pytest -m integration

2. **Django Integration Tests** (no Docker needed)
   - Use fixtures: django_client, request_factory
   - Tests Django components working together (admin, models, middleware)

Fixture Categories:
===================

Service Fixtures (require `make integration-up`):
- wait_for_prometheus: Prometheus metrics server
- wait_for_otel_collector: OpenTelemetry Collector
- wait_for_hyperdx: HyperDX observability platform
- wait_for_services: All of the above

Django Fixtures (no Docker needed):
- configure_django: Auto-configures Django for tests
- django_client: Django test client
- otel_endpoint: OTEL endpoint URL (for configuration, not connectivity)
- prometheus_url: Prometheus URL (for configuration, not connectivity)

Usage:
======
Tests that need external services should explicitly depend on wait_for_* fixtures.
This ensures tests FAIL (not skip) when services are unavailable, making it clear
that the test requires infrastructure.

Example:
    def test_metrics_in_prometheus(wait_for_prometheus, django_client):
        # This test will FAIL if Prometheus is not running
        ...

    def test_django_admin_works(django_client):
        # This test only needs Django, no Docker services
        ...
"""

import os
import time
from typing import Generator

import pytest
import requests

pytestmark = pytest.mark.integration

# Note: Django imports are done inside fixtures to ensure Django is configured first


def _check_service(service_name: str, port: str, max_retries: int = 30) -> bool:
    """Check if a specific service is available.

    Args:
        service_name: Name of the service (prometheus, hyperdx, or otel_collector)
        port: Port number to check
        max_retries: Maximum number of retry attempts

    Returns:
        True if service is available, False otherwise
    """
    import socket

    for i in range(max_retries):
        try:
            if service_name == "prometheus":
                response = requests.get(f"http://localhost:{port}/-/healthy", timeout=2)
                if response.status_code in (200, 404):  # 404 is ok for some endpoints
                    return True
            elif service_name == "hyperdx":
                response = requests.get(f"http://localhost:{port}/health", timeout=2)
                if response.status_code in (200, 404):
                    return True
            else:  # otel-collector - check if OTLP HTTP receiver is responding
                # Try to make a request to the OTLP endpoint
                # Even a 400/405 response means the service is up
                try:
                    response = requests.post(
                        f"http://localhost:{port}/v1/traces",
                        timeout=2,
                        json={},
                        headers={"Content-Type": "application/json"},
                    )
                    # Any HTTP response (even errors) means the service is running
                    if response.status_code in (200, 400, 405, 404, 415):
                        return True
                except requests.exceptions.ConnectionError:
                    # Connection refused means service is down
                    pass
                except requests.exceptions.RequestException:
                    # Other errors might mean service is up but rejecting request
                    # Try TCP connection as fallback
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(2)
                        result = sock.connect_ex(("localhost", int(port)))
                        sock.close()
                        if result == 0:  # 0 means connection successful
                            return True
                    except (socket.error, ValueError):
                        pass
        except (requests.exceptions.RequestException, socket.error, ValueError):
            if i < max_retries - 1:
                time.sleep(1)
    return False


# =============================================================================
# Service Wait Fixtures (require Docker services)
# =============================================================================


@pytest.fixture(scope="session")
def wait_for_prometheus() -> Generator[None, None, None]:
    """Wait for Prometheus service to be ready.

    This fixture REQUIRES Prometheus to be running. If not available,
    tests will FAIL (not skip) to ensure integration tests test against real services.

    Usage:
        def test_something(wait_for_prometheus, prometheus_url):
            # Test will fail if Prometheus is not running
            ...

    Start services with: make integration-up
    """
    port = os.getenv("PROMETHEUS_PORT", "9090")
    if not _check_service("prometheus", port):
        raise RuntimeError(
            f"Required Docker service Prometheus is not available on port {port}. "
            f"Please run 'make integration-up' to start the integration test stack."
        )
    yield


@pytest.fixture(scope="session")
def wait_for_otel_collector() -> Generator[None, None, None]:
    """Wait for OTEL Collector service to be ready.

    This fixture REQUIRES OTEL Collector to be running. If not available,
    tests will FAIL (not skip) to ensure integration tests test against real services.

    Usage:
        def test_tracing(wait_for_otel_collector, otel_http_endpoint):
            # Test will fail if OTEL Collector is not running
            ...

    Start services with: make integration-up
    """
    # Check the OTLP HTTP receiver port to verify collector is running
    http_port = os.getenv("OTEL_COLLECTOR_OTLP_HTTP_PORT", "4318")
    if not _check_service("otel_collector", http_port):
        raise RuntimeError(
            f"Required Docker service OTEL Collector is not available on HTTP port {http_port}. "
            f"Please run 'make integration-up' to start the integration test stack."
        )
    yield


@pytest.fixture(scope="session")
def wait_for_hyperdx() -> Generator[None, None, None]:
    """Wait for HyperDX service to be ready.

    This fixture REQUIRES HyperDX to be running. If not available,
    tests will FAIL (not skip) to ensure integration tests test against real services.

    Start services with: make integration-up
    """
    port = os.getenv("HYPERDX_PORT", "8080")
    if not _check_service("hyperdx", port):
        raise RuntimeError(
            f"Required Docker service HyperDX is not available on port {port}. "
            f"Please run 'make integration-up' to start the integration test stack."
        )
    yield


@pytest.fixture(scope="session")
def wait_for_services() -> Generator[None, None, None]:
    """Wait for all Docker services to be ready.

    This fixture REQUIRES all Docker services to be running. If services are not available,
    tests will FAIL (not skip) to ensure integration tests actually test against real services.

    For tests that only need specific services, use the individual fixtures:
    - wait_for_prometheus
    - wait_for_otel_collector
    - wait_for_hyperdx

    Start services with: make integration-up
    """
    services = {
        "otel_collector": os.getenv("OTEL_COLLECTOR_OTLP_HTTP_PORT", "4318"),
        "prometheus": os.getenv("PROMETHEUS_PORT", "9090"),
        "hyperdx": os.getenv("HYPERDX_PORT", "8080"),
    }

    unavailable_services = []

    for service_name, port in services.items():
        if not _check_service(service_name, port):
            unavailable_services.append(f"{service_name} (port {port})")

    if unavailable_services:
        raise RuntimeError(
            f"Required Docker services are not available: {', '.join(unavailable_services)}. "
            f"Please run 'make integration-up' to start the integration test stack."
        )

    yield


# =============================================================================
# Django Configuration (no Docker needed)
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def configure_django() -> None:
    """Configure Django settings for integration tests.

    This fixture is auto-used and does NOT require Docker services.
    It configures Django with:
    - In-memory SQLite database
    - observe_kit middleware stack
    - Required Django apps (auth, admin, contenttypes)
    """
    import django
    from django.conf import settings as django_settings

    if not django_settings.configured:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.integration.test_settings")

        django_settings.configure(
            DEBUG=True,
            SECRET_KEY="test-secret-key-for-integration-tests",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.admin",
                "rest_framework",
                "observe_kit",
                "observe_kit.audit",
            ],
            MIDDLEWARE=[
                "observe_kit.otel.middleware.TraceContextMiddleware",
                "observe_kit.context_middleware.RequestContextMiddleware",
                "observe_kit.context_middleware.UserLoggingContextMiddleware",
                "observe_kit.logging.middleware.RequestLoggingMiddleware",
                "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
                "observe_kit.sentry.middleware.SentryContextMiddleware",
                "observe_kit.drf.integration.DRFIntegrationMiddleware",
            ],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            ROOT_URLCONF="observe_kit.urls",
            AUTH_USER_MODEL="auth.User",
            USE_TZ=True,
            ALLOWED_HOSTS=["*", "testserver", "localhost"],
            REST_FRAMEWORK={
                "DEFAULT_RENDERER_CLASSES": [
                    "rest_framework.renderers.JSONRenderer",
                ],
            },
        )
        django.setup()

        # Create database tables
        from django.core.management import call_command
        from django.db import connection

        # Run migrations for Django apps
        call_command("migrate", verbosity=0, interactive=False)

        # Create tables for observe_kit.audit (no migrations)
        from observe_kit.audit.models import AuditLog

        # Try to create the table, ignore if it already exists
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(AuditLog)
        except Exception:
            # Table might already exist, which is fine
            pass


# =============================================================================
# Django Test Client Fixtures (no Docker needed)
# =============================================================================


@pytest.fixture
def django_client():
    """Django test client configured with observe_kit middleware.

    This fixture does NOT require Docker services. Use it for testing
    Django integration without external observability services.
    """
    from django.test import Client

    return Client()


@pytest.fixture
def request_factory():
    """Django request factory for creating mock requests.

    This fixture does NOT require Docker services.
    """
    from django.test import RequestFactory

    return RequestFactory()


# =============================================================================
# Endpoint URL Fixtures (configuration only, not connectivity)
# =============================================================================


@pytest.fixture
def otel_endpoint() -> str:
    """OTEL Collector gRPC endpoint URL.

    Note: This provides the URL for configuration purposes.
    For tests that need a running collector, also use wait_for_otel_collector.
    """
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


@pytest.fixture
def otel_http_endpoint() -> str:
    """OTEL Collector HTTP endpoint URL.

    Note: This provides the URL for configuration purposes.
    For tests that need a running collector, also use wait_for_otel_collector.
    """
    port = os.getenv("OTEL_COLLECTOR_OTLP_HTTP_PORT", "4318")
    return f"http://localhost:{port}"


@pytest.fixture
def prometheus_url() -> str:
    """Prometheus query URL.

    Note: This provides the URL for configuration purposes.
    For tests that need a running Prometheus, also use wait_for_prometheus.
    """
    port = os.getenv("PROMETHEUS_PORT", "9090")
    return f"http://localhost:{port}"


@pytest.fixture
def hyperdx_url() -> str:
    """HyperDX query URL.

    Note: This provides the URL for configuration purposes.
    For tests that need a running HyperDX, also use wait_for_hyperdx.
    """
    port = os.getenv("HYPERDX_PORT", "8080")
    return f"http://localhost:{port}"
