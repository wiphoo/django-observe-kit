"""Shared fixtures for unit tests."""

import contextlib
import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from observe_kit.settings import ObserveKitSettings

_OBSERVE_KIT_SETTINGS_DEFAULTS: dict[str, Any] = {
    "configured": True,
    "service_name": None,
    "otel_endpoint": None,
    "log_level": "INFO",
    "pii_level": "BASIC",
    "pii_levels": None,
    "sentry_dsn": None,
    "sentry_environment": "production",
    "sentry_traces_sample_rate": 0.0,
    "enabled": True,
    "db_tracking": True,
    "pii_hash_salt": "",
    "extra_drop_headers": frozenset(),
    "extra_mask_fields": frozenset(),
    "extra_hash_fields": frozenset(),
    "trusted_proxies": [],
    "otel_sample_rate": None,
    "metrics_auth": "none",
    "metrics_token": None,
    "trust_incoming_trace_context": False,
    "trusted_trace_sources": [],
    "validate_middleware_order": True,
    "metrics_max_label_cardinality": 1000,
}


def make_observe_kit_settings(**overrides: Any) -> ObserveKitSettings:
    """Build an ObserveKitSettings instance with sane test defaults."""
    return ObserveKitSettings(**{**_OBSERVE_KIT_SETTINGS_DEFAULTS, **overrides})  # type: ignore[arg-type]


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
def observe_kit_settings() -> Callable[[dict[str, Any]], contextlib.AbstractContextManager[None]]:
    """Temporarily set django.conf.settings.OBSERVE_KIT and restore it afterwards."""
    from django.conf import settings as django_settings

    @contextlib.contextmanager
    def _set(config: dict[str, Any]) -> Iterator[None]:
        original = getattr(django_settings, "OBSERVE_KIT", None)
        setattr(django_settings, "OBSERVE_KIT", config)
        try:
            yield
        finally:
            if original is None:
                delattr(django_settings, "OBSERVE_KIT")
            else:
                setattr(django_settings, "OBSERVE_KIT", original)

    return _set
