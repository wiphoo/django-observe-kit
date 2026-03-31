"""Root conftest.py - configures Django before any test collection.

This file ensures Django is configured BEFORE pytest collects test modules.
This is necessary because some modules (like observe_kit.drf) import Django
REST Framework at module level, which requires Django settings to be configured.
"""


def pytest_configure(config):
    """Configure Django settings before test collection."""
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            SECRET_KEY="test-secret-key-for-pytest-collection",
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
                "observe_kit.logging.middleware.RequestLoggingMiddleware",
                "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
                "observe_kit.context_middleware.RequestContextMiddleware",
                "observe_kit.context_middleware.UserLoggingContextMiddleware",
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

        # Run migrations to create database tables
        from django.core.management import call_command
        from django.db import connection

        call_command("migrate", verbosity=0, interactive=False)

        # Create tables for observe_kit.audit (no migrations file)
        from observe_kit.audit.models import AuditLog

        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(AuditLog)
        except Exception:
            # Table might already exist
            pass


def pytest_collection_modifyitems(config, items):
    """Add markers to integration tests."""
    import pytest

    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
