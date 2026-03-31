"""
Django settings for django_example project.

This example demonstrates basic observe_kit integration with Django.
"""

import os
from pathlib import Path

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-example-key-for-demonstration-only"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["*"]

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # observe_kit apps
    "observe_kit",
    "observe_kit.audit",
    # Example app
    "example_app",
]

MIDDLEWARE = [
    # observe_kit middleware (order matters!)
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    # Django middleware
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "django_example.urls"

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

WSGI_APPLICATION = "django_example.wsgi_application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ============================================================================
# observe_kit Configuration
# ============================================================================

# Configure JSON logging
from observe_kit.logging import configure_logging

configure_logging(
    level="INFO",
    pii_levels={
        "logs": "BASIC",
        "otel": "BASIC",
        "sentry": "SENSITIVE",
        "audit": "NONE",
    },
)

# Optional: Initialize tracing (uncomment if you have OTEL collector)
# from observe_kit.otel import init_tracing
# init_tracing(service_name="django-example")

# Optional: Initialize Sentry (uncomment if you have Sentry DSN)
# from observe_kit.sentry import init_sentry
# init_sentry(
#     dsn="https://your-key@o0.ingest.sentry.io/your-project",
#     environment="development",
# )

# Optional: Disable DB query tracking for performance
# import observe_kit.conf as observe_conf
# observe_conf.ENABLE_DB_TRACKING = False




