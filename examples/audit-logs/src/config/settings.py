from __future__ import annotations

import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OBSERVE_KIT_SERVICE_NAME = "example-audit-logs"

SECRET_KEY = "django-insecure-example-observe-kit"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "observe_kit",
    "observe_kit.audit",
    "demo_api",
]

MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "demo_api.middleware.TraceContextSyncMiddleware",
    "observe_kit.drf.integration.DRFIntegrationMiddleware",
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
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    }
]


def _postgres_database_config() -> dict[str, object] | None:
    engine = os.getenv("OBSERVE_KIT_DB_ENGINE", "").lower()
    if engine not in {"postgres", "postgresql"}:
        return None

    name = os.getenv("OBSERVE_KIT_DB_NAME", "observe_kit_audit")
    user = os.getenv("OBSERVE_KIT_DB_USER", "observe_kit")
    password = os.getenv("OBSERVE_KIT_DB_PASSWORD", "observe_kit")
    host = os.getenv("OBSERVE_KIT_DB_HOST", "127.0.0.1")
    port = os.getenv("OBSERVE_KIT_DB_PORT", "5432")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": port,
        "OPTIONS": {
            "application_name": DEFAULT_OBSERVE_KIT_SERVICE_NAME,
        },
        "CONN_HEALTH_CHECKS": True,
    }


DATABASES = {
    "default": _postgres_database_config()
    or {
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

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "observe_kit.drf.observed_exception_handler",
}

OBSERVE_KIT = {
    "SERVICE_NAME": (
        os.getenv("OBSERVE_KIT_SERVICE_NAME")
        or (
            DEFAULT_OBSERVE_KIT_SERVICE_NAME
            if os.getenv("OBSERVE_KIT_OTEL_ENDPOINT")
            else None
        )
    ),
    "OTEL_ENDPOINT": os.getenv("OBSERVE_KIT_OTEL_ENDPOINT"),
    "OTEL_SAMPLE_RATE": os.getenv("OBSERVE_KIT_OTEL_SAMPLE_RATE"),
    "LOG_LEVEL": "INFO",
    "DB_TRACKING": True,
    "PII_HASH_SALT": os.getenv("OBSERVE_KIT_PII_HASH_SALT", "example-salt"),
    "PII_LEVELS": {
        "logs": "BASIC",
        "otel": "BASIC",
        "audit": "NONE",
    },
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
