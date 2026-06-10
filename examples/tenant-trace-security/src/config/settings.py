from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OBSERVE_KIT_SERVICE_NAME = "example-tenant-trace-security"

SECRET_KEY = "django-insecure-example-observe-kit"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver", ".example.test"]

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
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

OBSERVE_KIT = {
    "SERVICE_NAME": os.getenv("OBSERVE_KIT_SERVICE_NAME", DEFAULT_OBSERVE_KIT_SERVICE_NAME),
    "LOG_LEVEL": "INFO",
    "PII_HASH_SALT": "example-salt",
    "TRUSTED_PROXIES": ["10.0.0.1"],
    "METRICS_MAX_LABEL_CARDINALITY": 1,
}
