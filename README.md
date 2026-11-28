# Django Observe Kit

[![CI](https://github.com/wiphoo/django-observe-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/wiphoo/django-observe-kit/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.2+](https://img.shields.io/badge/django-5.2+-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/wiphoo/django-observe-kit/blob/main/LICENSE)

Drop-in observability toolkit for Django/DRF/Wagtail: request context, structured JSON logging, OpenTelemetry tracing, Prometheus metrics, Sentry, and audit logs, with PII rules for the data Observe Kit collects.

**Status: beta.** This is a 0.x release, and its API and defaults may change between minor versions.

## What is not sanitized

PII scrubbing is a safety net, not a guarantee. Keep secrets and personal data out of what you log or send. In particular:

- **Log records are only partly cleaned.** On the outputs Observe Kit configures (its JSON console output and its OpenTelemetry log handler), listed field names are dropped, masked or redacted, emails are masked and URL credentials are scrubbed. Other free text is written as given: `password=hunter2` in a sentence, bare tokens, phone numbers, names, positional arguments such as `"password %s"`, and names not on the list such as `client_secret`.
- **Other handlers get the original record.** This includes your own handlers and Django's `django` and `django.server` handlers.
- **Do not log `request.META` or large or sensitive objects.** Only some `META` entries are treated, and under `runserver` it also holds the process environment. Sequences are opened, so logging a Django paginator `Page` runs its query (and in `extra`, writes every row).
- **Sanitizing costs CPU.** In our microbenchmarks a record took roughly 5 to 11 times the CPU time of unsanitized JSON console output, and each output pays it again. There is no limit on the total text size.
- **The audit log redacts only some credential names.** `audit()` stores values under names such as `password` and `token` as `[Filtered]`, but other names such as `client_secret`, and credentials inside strings, are stored as given.
- **Sentry scrubbing has gaps**, including attachments, phone numbers and bare `token=...` in free text, and parameter names such as `client_secret`.

Read all [known limitations](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#known-limitations) before relying on it.

## Features

- **Request context and logging:** JSON logs with request fields, including a `request_complete` event.
- **Tracing:** OpenTelemetry traces and log export over OTLP/HTTP, and an `X-Trace-Id` response header. Incoming trace headers are ignored unless you trust them.
- **Metrics:** Prometheus request, latency and database metrics on `/metrics`.
- **Sentry:** error and transaction scrubbing at a configurable PII level. Sentry metrics and logs are disabled because they would bypass it.
- **DRF, audit log and Wagtail:** optional integrations.

## Requirements

- Python 3.10 or later
- Django 5.2 or later and Django REST framework 3.16 or later (installed as a dependency)
- `sentry-sdk` 2.x only (`>=2.66.1,<3`, installed as a dependency)
- For the `wagtail` extra, Python 3.11.5 or later is recommended: older interpreters resolve [releases with security advisories](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#wagtail-extra-on-older-python)

## Installation

```bash
pip install django-observe-kit
```

For the Wagtail integration, install `"django-observe-kit[wagtail]"` instead.

## Quickstart

**1. Settings.** Add this to the end of your project's `settings.py`. Keep the middleware in this order, and after Django's `AuthenticationMiddleware` so that logs include the user ID.

```python
# settings.py (append)
import os

INSTALLED_APPS += ["observe_kit"]

MIDDLEWARE += [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
]

OBSERVE_KIT = {
    # Enables tracing. Traces and logs go to an OTLP/HTTP collector.
    "SERVICE_NAME": "my-app",
    "OTEL_ENDPOINT": "http://localhost:4318",
    # Salt for hashed PII values; set a secret value per environment.
    "PII_HASH_SALT": os.environ.get("PII_HASH_SALT", ""),
    # /metrics is open to anyone unless you set this.
    "METRICS_AUTH": "token",
    "METRICS_TOKEN": os.environ.get("METRICS_TOKEN"),
}
```

With `OBSERVE_KIT` set, logging, tracing and Sentry are initialised at startup. Sentry starts when the `SENTRY_DSN` environment variable (or setting) is present. If no collector is listening at `OTEL_ENDPOINT`, the exporter logs connection errors and commands take a few extra seconds to exit; remove `SERVICE_NAME` to run without tracing, and make sure `OTEL_SERVICE_NAME` is not set.

**2. Metrics URL.** Include `observe_kit.urls`, which serves `/metrics`:

```python
# urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("observe_kit.urls")),
]
```

**3. Run it.**

```bash
export PII_HASH_SALT=change-me METRICS_TOKEN=change-me
python manage.py check
python manage.py runserver
```

In another terminal:

```bash
curl -H "Authorization: Bearer change-me" http://127.0.0.1:8000/metrics
```

The server prints a JSON `request_complete` log line for the request.

### Manual initialisation

To initialise from code instead, leave out the whole `OBSERVE_KIT` dict and call the functions from an `AppConfig.ready()`, which runs after Django has configured its logging. Other options come from [environment variables or defaults](https://github.com/wiphoo/django-observe-kit/blob/main/docs/configuration.md), so set `OBSERVE_KIT_METRICS_AUTH=token` and `OBSERVE_KIT_METRICS_TOKEN` to keep `/metrics` protected.

```python
# mysite/apps.py
import os

from django.apps import AppConfig


class MySiteConfig(AppConfig):
    name = "mysite"  # your project package or one of your apps

    def ready(self) -> None:
        from observe_kit import configure_logging, init_sentry, init_tracing

        configure_logging(level="INFO")
        init_tracing(service_name="my-app", endpoint="http://localhost:4318")
        if os.environ.get("SENTRY_DSN"):
            init_sentry(dsn=os.environ["SENTRY_DSN"], environment="production")
```

```python
# manual initialisation (append to settings.py)
INSTALLED_APPS += ["mysite"]
```

### Exporting logs at shutdown

For the log handler that `init_tracing()` adds, **`handler.flush()`, `logging.shutdown()` and `logging.config.dictConfig()` do not export queued records.** (Flushing there could keep a process from ever exiting.)

- Queued records are still exported periodically.
- A `LoggerProvider` with `shutdown_on_exit=True` (the default, including the one `init_tracing()` creates) also exports them at a normal exit. With no collector listening, that can delay the exit by about `OTEL_EXPORTER_OTLP_TIMEOUT` (default 10 seconds).
- A process that ends without running `atexit` handlers **loses its queued records**, for example a `multiprocessing` fork child (which leaves through `os._exit`) or a process killed by an unhandled `SIGTERM`.

## Optional integrations

**DRF** (view-set action names on spans and routes, and an exception handler). Add to `settings.py` after the Quickstart block:

```python
# DRF (append)
INSTALLED_APPS += ["rest_framework"]
MIDDLEWARE.insert(
    MIDDLEWARE.index("observe_kit.sentry.middleware.SentryContextMiddleware"),
    "observe_kit.drf.integration.DRFIntegrationMiddleware",
)
REST_FRAMEWORK = {"EXCEPTION_HANDLER": "observe_kit.drf.observed_exception_handler"}
```

**Audit log.** Add the app, then run `python manage.py migrate`:

```python
# audit log (append)
INSTALLED_APPS += ["observe_kit.audit"]
```

Logins and logouts are then recorded, and you can record your own events with `from observe_kit.audit import audit`.

**Wagtail.** In an existing Wagtail project with the audit log enabled, add `"observe_kit.wagtail_integration"` to `INSTALLED_APPS` to audit and count page publish, unpublish and delete.

## Documentation

- [Configuration reference](https://github.com/wiphoo/django-observe-kit/blob/main/docs/configuration.md)
- [Middleware reference](https://github.com/wiphoo/django-observe-kit/blob/main/docs/middleware.md)
- [PII sanitization and known limitations](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md)
- [HyperDX quickstart](https://github.com/wiphoo/django-observe-kit/blob/main/docs/HYPERDX_QUICKSTART.md)
- [Changelog](https://github.com/wiphoo/django-observe-kit/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/wiphoo/django-observe-kit/blob/main/SECURITY.md): report vulnerabilities privately

## Contributing

See [CONTRIBUTING.md](https://github.com/wiphoo/django-observe-kit/blob/main/CONTRIBUTING.md) for development setup and tests. Maintainers: see the [release checklist](https://github.com/wiphoo/django-observe-kit/blob/main/docs/releasing.md).

## License

[MIT](https://github.com/wiphoo/django-observe-kit/blob/main/LICENSE)
