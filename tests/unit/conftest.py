"""Shared fixtures for unit tests."""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def configure_django():
    """Configure Django settings for unit tests.

    Note: We don't include observe_kit.audit in INSTALLED_APPS by default
    because it requires Django models to be fully configured, which causes
    circular dependency issues. Tests that need audit models should configure
    Django separately or use integration tests.
    """
    import django
    from django.conf import settings

    if not settings.configured:
        # Set DJANGO_SETTINGS_MODULE to avoid warnings
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.unit.test_settings")

        settings.configure(
            DEBUG=True,
            SECRET_KEY="test-secret-key",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "observe_kit",
                # Note: observe_kit.audit is excluded to avoid AppRegistryNotReady
                # Include it only in tests that specifically need it
                "rest_framework",
            ],
            AUTH_USER_MODEL="auth.User",
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            REST_FRAMEWORK={},
            USE_TZ=True,
            ALLOWED_HOSTS=["*", "testserver", "localhost"],
        )
        django.setup()
