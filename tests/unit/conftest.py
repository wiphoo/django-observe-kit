"""Shared fixtures for unit tests."""

import contextlib
import os
from typing import Any, Generator

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


@pytest.fixture
def observe_kit_settings() -> Generator[Any, None, None]:
    """Temporarily set django.conf.settings.OBSERVE_KIT and restore it afterwards."""
    from django.conf import settings as django_settings

    @contextlib.contextmanager
    def _set(config: dict) -> Generator[None, None, None]:  # type: ignore[misc]
        original = getattr(django_settings, "OBSERVE_KIT", None)
        django_settings.OBSERVE_KIT = config  # type: ignore[attr-defined]
        try:
            yield
        finally:
            if original is None:
                del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
            else:
                django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]

    yield _set
