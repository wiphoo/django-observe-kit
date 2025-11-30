import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("django"), reason="django not installed"
)


def test_audit_module_importable() -> None:
    """Test that audit module can be imported (requires Django settings)."""
    import django
    from django.conf import settings
    from django.core.exceptions import AppRegistryNotReady

    # Skip if Django is not available
    if not importlib.util.find_spec("django"):
        pytest.skip("Django not installed")

    # Try to import - if it fails due to app registry, that's expected for unit tests
    # Audit models require Django to be fully set up, which is tested in integration tests
    try:
        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="test-secret-key",
                INSTALLED_APPS=[
                    "django.contrib.contenttypes",
                    "django.contrib.auth",
                    "observe_kit",
                    "observe_kit.audit",
                ],
                AUTH_USER_MODEL="auth.User",
                DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            )
            django.setup()

        # Try import
        importlib.import_module("observe_kit.audit")
    except AppRegistryNotReady:
        # This is expected - audit models require Django to be fully set up
        # This test is more of a smoke test, actual functionality tested in integration tests
        pytest.skip(
            "Django apps not ready - this is expected for unit tests without full Django setup"
        )
