"""Shared fixtures for integration tests."""

import os
import time
from typing import Generator

import pytest
import requests
from django.conf import settings
from django.test import Client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def wait_for_services() -> Generator[None, None, None]:
    """Wait for Docker services to be ready."""
    services = {
        "otel_collector": os.getenv("OTEL_COLLECTOR_OTLP_HTTP_PORT", "4318"),
        "prometheus": os.getenv("PROMETHEUS_PORT", "9090"),
        "jaeger": os.getenv("JAEGER_UI_PORT", "16686"),
    }

    max_retries = 30
    for service_name, port in services.items():
        for i in range(max_retries):
            try:
                if service_name == "prometheus":
                    response = requests.get(f"http://localhost:{port}/-/healthy", timeout=2)
                elif service_name == "jaeger":
                    response = requests.get(f"http://localhost:{port}", timeout=2)
                else:  # otel-collector
                    response = requests.get(f"http://localhost:{port}/metrics", timeout=2)

                if response.status_code in (200, 404):  # 404 is ok for some endpoints
                    break
            except requests.exceptions.RequestException:
                if i == max_retries - 1:
                    pytest.skip(f"Service {service_name} not available on port {port}")
                time.sleep(1)

    yield
    # Cleanup if needed


@pytest.fixture
def django_client(wait_for_services: Generator[None, None, None]) -> Client:
    """Django test client configured with observe_kit middleware."""
    # Configure Django settings if not already configured
    if not settings.configured:
        from django.conf import settings as django_settings

        django_settings.configure(
            DEBUG=True,
            SECRET_KEY="test-secret-key",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
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
            ],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            ROOT_URLCONF="observe_kit.urls",
        )

    return Client()


@pytest.fixture
def otel_endpoint() -> str:
    """OTEL Collector endpoint."""
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


@pytest.fixture
def otel_http_endpoint() -> str:
    """OTEL Collector HTTP endpoint."""
    return os.getenv("OTEL_EXPORTER_OTLP_HTTP_ENDPOINT", "http://localhost:4318")


@pytest.fixture
def prometheus_url() -> str:
    """Prometheus query URL."""
    port = os.getenv("PROMETHEUS_PORT", "9090")
    return f"http://localhost:{port}"


@pytest.fixture
def jaeger_url() -> str:
    """Jaeger query URL."""
    port = os.getenv("JAEGER_UI_PORT", "16686")
    return f"http://localhost:{port}"
