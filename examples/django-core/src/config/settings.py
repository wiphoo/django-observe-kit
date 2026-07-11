from __future__ import annotations

import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OBSERVE_KIT_SERVICE_NAME = "example-django-core"

SECRET_KEY = "django-insecure-example-observe-kit"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "observe_kit",
    "core",
]

MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "core.middleware.TraceContextSyncMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

OBSERVE_KIT = {
    # Only enable OTEL tracing/log export when an OTLP endpoint is configured;
    # otherwise the exporters fall back to localhost and emit connection-refused
    # errors. This example does not run a collector by default.
    "SERVICE_NAME": (
        os.getenv("OBSERVE_KIT_SERVICE_NAME")
        or (DEFAULT_OBSERVE_KIT_SERVICE_NAME if os.getenv("OBSERVE_KIT_OTEL_ENDPOINT") else None)
    ),
    "OTEL_ENDPOINT": os.getenv("OBSERVE_KIT_OTEL_ENDPOINT"),
    "LOG_LEVEL": os.getenv("OBSERVE_KIT_LOG_LEVEL", "INFO"),
    "PII_HASH_SALT": "example-salt",
}


def _env_flag(name: str, default: str = "1") -> bool:
    # An explicitly-empty value (e.g. ``FLAG=``) reads as "off", not "on".
    return os.getenv(name, default).strip().lower() not in {"", "0", "false", "no"}


def _console_spans_enabled() -> bool:
    explicit = os.getenv("OBSERVE_KIT_ENABLE_CONSOLE_SPANS")
    if explicit is not None:
        return _env_flag("OBSERVE_KIT_ENABLE_CONSOLE_SPANS", explicit)
    return not bool(os.getenv("OBSERVE_KIT_OTEL_ENDPOINT"))


def _enable_console_span_export() -> None:
    """Install a local SDK tracer provider with a console exporter.

    Without an OTLP endpoint the library skips ``init_tracing``, leaving
    OpenTelemetry's no-op provider — whose span context is all-zeroes, so
    ``X-Trace-Id`` would render ``00000000000000000000000000000000``. Setting a
    real ``TracerProvider`` here gives requests genuine trace IDs (the point of
    this example) while a ``ConsoleSpanExporter`` keeps spans local — no OTLP
    connection, no localhost connection-refused noise.
    """
    if not _console_spans_enabled():
        return

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)

    if getattr(provider, "_example_console_exporter_enabled", False):
        return

    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    setattr(provider, "_example_console_exporter_enabled", True)


_enable_console_span_export()
