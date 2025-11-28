# Django Observe Kit

Drop-in observability for Django, DRF, and Wagtail. Observe Kit provides
request-scoped context, JSON logging, OpenTelemetry tracing, Prometheus metrics,
Sentry enrichment, database timing, and audit logging with PII-aware defaults.

## Features

- **Unified request context** stored in contextvars for logs, metrics, traces, and audit entries.
- **PII-safe sanitization** for headers and query params with BASIC/SENSITIVE levels.
- **Multi-tenant awareness** via tenant resolver (django-tenants, header, or subdomain).
- **Tracing** with OTEL spans enriched with tenant/user/db metadata and an `X-Trace-Id` response header.
- **Metrics** for HTTP requests, DB time/query counts, Wagtail events, and audit events via Prometheus client.
- **Logging** configuration helper that emits JSON with canonical `request_complete` events.
- **Sentry** initializer with PII scrubbing and middleware that attaches trace/tenant/http tags.
- **DRF** exception handler that suppresses noisy ValidationErrors and captures 5xx errors.
- **Wagtail** hooks for publish/unpublish/delete that emit spans, metrics, audit entries, and structured logs.
- **Audit log** model and helper that records actor/action/object with tenant and request metadata.

## Project layout

```
observe_kit/
├─ src/observe_kit/              # library code
├─ tests/                        # pytest suite
└─ pyproject.toml                # packaging metadata
```

## Installation

```bash
pip install -e .
```

### Developer setup

Use [uv](https://github.com/astral-sh/uv) for quick editable installs with the dev extras
enabled, which include Ruff for linting and Pytest for the test suite:

```bash
uv pip install -e .[dev]
```

Helpful `make` targets (override `UV` if you prefer `pip`/`python`):

```bash
make install   # uv pip install -e .[dev]
make lint      # ruff check src tests
make format    # ruff format src tests
make test      # pytest
```

Run Ruff locally to keep the codebase clean:

```bash
ruff check src tests
```

## Quick start

1. **Enable middleware (order matters):**

```python
MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    # ... your existing middleware ...
]
```

2. **Configure logging:**

```python
from observe_kit.logging import configure_logging

configure_logging(level="INFO", pii_level="BASIC")
```

3. **Initialize tracing and Sentry (optional):**

```python
from observe_kit.otel import init_tracing
from observe_kit.sentry import init_sentry

init_tracing(service_name="my-service")
init_sentry(dsn="https://key@o0.ingest.sentry.io/0", environment="dev")
```

4. **Expose Prometheus metrics and health:**

```python
from django.urls import include, path
from observe_kit import urls as observe_urls

urlpatterns = [
    path("", include(observe_urls)),
]
```

5. **Wire DRF exception handler:**

```python
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "observe_kit.drf.observed_exception_handler",
}
```

6. **Install the audit app:**

```python
INSTALLED_APPS = [
    # ...
    "observe_kit",
    "observe_kit.audit",
]
```

## Canonical log event

`request_complete` events are emitted with the following fields:

- method
- path
- route
- status
- duration_ms
- tenant_id
- user_id
- trace_id
- db_queries
- db_time_ms

## Metrics emitted

- `http_requests_total{method,route,status,tenant}`
- `http_request_duration_seconds{method,route,status,tenant}`
- `db_queries_per_request{route,tenant}`
- `db_time_per_request_seconds{route,tenant}`
- `wagtail_pages_published_total{tenant}`
- `wagtail_pages_unpublished_total{tenant}`
- `wagtail_pages_deleted_total{tenant}`
- `audit_events_total{tenant}`

## PII levels

- `NONE`: No sanitization
- `BASIC`: Drop auth cookies/tokens, mask email/phone
- `SENSITIVE`: Basic + hash user agent and IP

## Health and metrics endpoints

Expose `/metrics` with `metrics_view.as_view()` or include `observe_kit.urls` for
both `/metrics` and `/healthz`. A simple `/healthz` view returns HTTP 200 for
liveness checks.
