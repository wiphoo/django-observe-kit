"""Django settings for the Wagtail Heroku container example."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Core Django settings
# ---------------------------------------------------------------------------

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-heroku-example")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS: List[str] = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

CSRF_TRUSTED_ORIGINS: List[str] = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"

INSTALLED_APPS = [
    # Wagtail apps
    "wagtail.contrib.forms",
    "wagtail.contrib.redirects",
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "modelcluster",
    "taggit",
    # Django apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # observe_kit
    "observe_kit",
    "observe_kit.audit",
    # Example app
    "cms_app",
]

MIDDLEWARE = [
    # observe_kit middleware (order matters!)
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    # Django/Wagtail middleware
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
]

ROOT_URLCONF = "wagtail_heroku_example.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "wagtail_heroku_example.wsgi.application"
ASGI_APPLICATION = "wagtail_heroku_example.asgi.application"

# ---------------------------------------------------------------------------
# Database (Neon / Postgres via DATABASE_URL)
# ---------------------------------------------------------------------------

_CONN_MAX_AGE = int(os.getenv("DB_CONN_MAX_AGE", "600"))
_DATABASE_URL = os.getenv("DATABASE_URL")

if _DATABASE_URL:
    DATABASES: Dict[str, Dict[str, object]] = {
        "default": dj_database_url.parse(
            _DATABASE_URL,
            conn_max_age=_CONN_MAX_AGE,
            ssl_require=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ---------------------------------------------------------------------------
# Auth / Intl
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

WAGTAIL_SITE_NAME = "Observe Kit Heroku Demo"
BASE_URL = os.getenv("DJANGO_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Logging & observability configuration
# ---------------------------------------------------------------------------

from observe_kit.logging import configure_logging

configure_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    pii_levels={
        "logs": os.getenv("PII_LEVEL_LOGS", "BASIC"),
        "otel": os.getenv("PII_LEVEL_OTEL", "BASIC"),
        "sentry": os.getenv("PII_LEVEL_SENTRY", "SENSITIVE"),
        "audit": os.getenv("PII_LEVEL_AUDIT", "NONE"),
    },
)

# Sentry integration ---------------------------------------------------------

SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    from observe_kit.sentry import init_sentry

    def _float_env(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except ValueError:
            return default

    init_sentry(
        dsn=SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=_float_env("SENTRY_TRACES_SAMPLE_RATE", 0.2),
        release=os.getenv("HEROKU_RELEASE_VERSION") or os.getenv("HEROKU_SLUG_COMMIT"),
    )

# OpenTelemetry / HyperDX ----------------------------------------------------

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "wagtail-heroku")

if OTEL_ENDPOINT:
    from observe_kit.otel import init_tracing

    resource_attributes = {
        "deployment.environment": os.getenv("SENTRY_ENVIRONMENT", "production"),
    }
    if app_name := os.getenv("HEROKU_APP_NAME"):
        resource_attributes["heroku.app_name"] = app_name
    if dyno := os.getenv("DYNO"):
        resource_attributes["heroku.dyno"] = dyno
    if release := os.getenv("HEROKU_RELEASE_VERSION"):
        resource_attributes["heroku.release"] = release

    init_tracing(
        service_name=OTEL_SERVICE_NAME,
        endpoint=OTEL_ENDPOINT,
        resource_attributes=resource_attributes,
    )
