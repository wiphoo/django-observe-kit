"""Smoke-test an installed wheel against the latest resolvable dependencies.

uv.lock pins versions, so the test suite cannot see a new dependency release
that breaks users. Run this from a clean venv holding only the built wheel.
"""

from importlib.metadata import version

import django
from django.conf import settings

settings.configure()
django.setup()

import sentry_sdk  # noqa: E402

from observe_kit.sentry.config import init_sentry  # noqa: E402

# Dummy DSN on the discard port: nothing is sent anywhere.
init_sentry(dsn="http://k@127.0.0.1:9/1", environment="smoke", traces_sample_rate=0.0)
if not sentry_sdk.get_client().is_active():
    raise SystemExit("init_sentry() left no active Sentry client")

print(f"smoke OK (sentry-sdk {version('sentry-sdk')})")
