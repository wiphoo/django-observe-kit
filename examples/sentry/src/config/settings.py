from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OBSERVE_KIT_SERVICE_NAME = "example-sentry"

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

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

OBSERVE_KIT = {
    # This demo focuses on Sentry, not traces. Only enable OTEL tracing/log
    # export when an OTLP endpoint is explicitly configured — otherwise the
    # exporters fall back to localhost and emit connection-refused errors on a
    # Sentry-only setup. Mirrors the OTEL/HyperDX examples' gating.
    "SERVICE_NAME": (
        os.getenv("OBSERVE_KIT_SERVICE_NAME")
        or (DEFAULT_OBSERVE_KIT_SERVICE_NAME if os.getenv("OBSERVE_KIT_OTEL_ENDPOINT") else None)
    ),
    "OTEL_ENDPOINT": os.getenv("OBSERVE_KIT_OTEL_ENDPOINT"),
    "SENTRY_DSN": os.getenv("OBSERVE_KIT_SENTRY_DSN"),
    "SENTRY_ENVIRONMENT": os.getenv("OBSERVE_KIT_SENTRY_ENVIRONMENT", "development"),
    "SENTRY_TRACES_SAMPLE_RATE": os.getenv("OBSERVE_KIT_SENTRY_TRACES_SAMPLE_RATE", "1.0"),
    "LOG_LEVEL": "INFO",
    "PII_HASH_SALT": os.getenv("OBSERVE_KIT_PII_HASH_SALT", "example-salt"),
    "PII_LEVELS": {
        "sentry": "SENSITIVE",
        "logs": "BASIC",
    },
}
