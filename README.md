# Django Observe Kit

[![CI](https://github.com/wiphoo/Django-Observe_Kit/actions/workflows/ci.yml/badge.svg)](https://github.com/wiphoo/Django-Observe_Kit/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 3.2+](https://img.shields.io/badge/django-3.2+-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

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
- **DRF** exception handler that logs ValidationErrors but doesn't send them to Sentry (to reduce noise), and captures 5xx errors as exceptions. Automatic ViewSet action detection with span naming format `drf.<ViewSet>.<action>`.
- **Wagtail** hooks for publish/unpublish/delete that emit spans, metrics, audit entries, and structured logs.
- **Audit log** model and helper that records actor/action/object with tenant, trace_id, and request metadata.

## Project layout

```
observe_kit/
├─ src/observe_kit/              # library code
├─ tests/                        # pytest suite
│  ├─ unit/                      # unit tests (no external dependencies)
│  ├─ integration/               # integration tests (require docker stack)
│  └─ e2e/                       # end-to-end tests
├─ docker/compose/               # Docker Compose files
│  ├─ integration.yml            # Integration test stack
│  ├─ configs/                   # Service configurations
│  │  ├─ otel-collector.yaml     # OTEL Collector config
│  │  └─ prometheus.yml          # Prometheus config
│  └─ scripts/                   # Initialization scripts
├─ examples/                     # Example projects
│  ├─ django_example/            # Basic Django integration
│  ├─ drf_example/               # DRF integration
│  └─ wagtail_example/           # Wagtail integration
├─ docs/internal/                # Internal development docs
├─ Makefile                      # Development commands
├─ pyproject.toml                # Packaging & tool configs
├─ CONTRIBUTING.md               # Contribution guidelines
└─ CODE_OF_CONDUCT.md            # Community guidelines
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

### Setup
```bash
make init              # Initialize project (install deps, setup pre-commit)
make install           # Install dependencies
```

### Development
```bash
make lint              # Run ruff linting
make format            # Format code with ruff
make typecheck         # Run mypy type checking
```

### Testing
```bash
make test              # Run the default unit test suite
make test-unit         # Run unit tests only
make test-int          # Run integration tests (requires docker stack)
make test-e2e          # Run E2E tests
make test-all          # Run all test suites
```

### Integration Testing
```bash
make integration-up    # Start Docker Compose integration stack
make integration-down  # Stop Docker Compose integration stack
```

### Packaging
```bash
make build             # Build distribution package
make publish           # Publish to PyPI (with safety checks)
```

### Cleanup
```bash
make clean             # Remove cache files and build artifacts
```

Run Ruff locally to keep the codebase clean:

```bash
ruff check src tests
```

## Testing

The test suite is organized into three categories:

- **Unit tests** (`tests/unit/`): Fast, isolated tests that don't require external services
- **Integration tests** (`tests/integration/`): Tests that require Docker Compose stack (OTEL Collector, Prometheus, etc.)
- **E2E tests** (`tests/e2e/`): End-to-end workflow tests

### Running Tests

```bash
# Run the default unit suite with the coverage gate
make test

# Run only unit tests (fast)
make test-unit

# Run integration tests (requires docker stack)
make integration-up    # Start services first
make test-int
make integration-down  # Stop services

# Run E2E tests
make test-e2e

# Run all test suites
make test-all
```

### Integration Test Setup

Integration tests require a Docker Compose stack with:
- OTEL Collector (for trace/metric collection)
- Prometheus (for metrics)
- HyperDX (for trace/log visualization)
- ClickHouse (for data storage)
- MongoDB (for HyperDX sessions)

Start the stack with:
```bash
make integration-up
```

The stack will be available at:
- OTEL Collector: `http://localhost:4317` (gRPC), `http://localhost:4318` (HTTP)
- Prometheus: `http://localhost:9090`
- HyperDX UI: `http://localhost:8080`
- ClickHouse: `http://localhost:8123`

See `docker/compose/README.md` for more details.

## Examples

Complete working examples are available in the `examples/` directory:

- **Django Example** (`examples/django_example/`) - Basic Django integration
- **DRF Example** (`examples/drf_example/`) - Django REST Framework integration
- **Wagtail Example** (`examples/wagtail_example/`) - Wagtail CMS integration

See `examples/README.md` for details and `examples/QUICK_START.md` for quick setup instructions.

## Quick start

1. **Enable middleware (order matters):**

```python
MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "observe_kit.drf.integration.DRFIntegrationMiddleware",  # Optional: for DRF ViewSet action detection
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    # ... your existing middleware ...
]
```

2. **Configure logging:**

```python
from observe_kit.logging import configure_logging

# Simple configuration (uses BASIC for all sinks)
configure_logging(level="INFO", pii_level="BASIC")

# Or configure per-sink PII levels
configure_logging(
    level="INFO",
    pii_levels={
        "logs": "BASIC",
        "otel": "BASIC",
        "sentry": "SENSITIVE",  # More restrictive for Sentry
        "audit": "NONE",  # No sanitization for audit logs
    }
)
```

3. **Initialize tracing and Sentry (optional):**

```python
from observe_kit.otel import init_tracing
from observe_kit.sentry import init_sentry

init_tracing(service_name="my-service")
# Sentry will use per-sink PII config if pii_level not specified
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

7. **Run migrations:**

```bash
python manage.py makemigrations observe_kit
python manage.py migrate
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

### Per-sink PII configuration

You can configure different PII levels for different observability sinks:

- `logs`: PII level for structured logging
- `otel`: PII level for OpenTelemetry spans
- `sentry`: PII level for Sentry error reporting
- `audit`: PII level for audit log entries

Example:

```python
from observe_kit import PiiConfig, set_pii_config

# Configure per-sink PII levels
config = PiiConfig(levels={
    "logs": "BASIC",
    "otel": "BASIC",
    "sentry": "SENSITIVE",  # More restrictive for error reporting
    "audit": "NONE",  # No sanitization for compliance
})
set_pii_config(config)
```

## Metrics endpoint

Expose `/metrics` with `metrics_view.as_view()` or include `observe_kit.urls`.
The packaged URLconf currently exposes the Prometheus metrics endpoint only.

## Advanced Usage

### Custom Span Names

You can customize span names by updating the context route:

```python
from observe_kit import get_request_context, set_request_context

context = get_request_context()
context.route = "custom.route.name"
set_request_context(context)
```

### Adding Custom Attributes to Spans

Add custom attributes to OTEL spans:

```python
from observe_kit import get_request_context
from observe_kit.otel import enrich_span
from opentelemetry import trace

# Get current span
span = trace.get_current_span()
if span:
    span.set_attribute("custom.attribute", "value")
    # Or use the enrich_span helper which includes context
    enrich_span(span)
```

### Using the audit() Helper

Record custom audit events:

```python
from observe_kit.audit import audit

# In a view or signal handler
audit(
    actor=request.user,
    action="custom_action",
    obj=some_model_instance,
    extra={"custom_field": "value"},
    request=request
)
```

### Disabling DB Query Tracking

For high-traffic sites, you can disable DB query tracking to improve performance:

```python
# In your Django settings
from observe_kit.conf import ENABLE_DB_TRACKING
import observe_kit.conf as observe_conf

observe_conf.ENABLE_DB_TRACKING = False
```

### Request Body Sanitization

Request and response bodies are automatically excluded from logs to prevent PII exposure. The library includes guards to prevent accidental body logging:

```python
from observe_kit.logging.filters import get_log_extra, sanitize_log_data

# get_log_extra() automatically filters out body fields
extra = get_log_extra("event", request_body="...")  # request_body is omitted

# Or manually sanitize data
safe_data = sanitize_log_data({"body": "...", "other": "data"})
# Result: {"body": "[BODY_OMITTED] Request/response bodies are never logged...", "other": "data"}
```

### Configuration Validation

All init functions now validate configuration and raise `ConfigurationError` on invalid input:

```python
from observe_kit.otel import init_tracing, ConfigurationError
from observe_kit.sentry import init_sentry
from observe_kit.logging import configure_logging

try:
    init_tracing(service_name="")  # Raises ConfigurationError
    init_sentry(dsn="invalid")  # Raises ConfigurationError
    configure_logging(level="INVALID")  # Raises ConfigurationError
except ConfigurationError as e:
    print(f"Configuration error: {e}")
```

### Per-Sink PII Configuration

Configure different PII levels programmatically:

```python
from observe_kit import PiiConfig, set_pii_config, get_pii_config

# Set per-sink levels
config = PiiConfig(levels={
    "logs": "BASIC",
    "otel": "BASIC",
    "sentry": "SENSITIVE",
    "audit": "NONE",
})
set_pii_config(config)

# Get current config
current_config = get_pii_config()
sentry_level = current_config.get_level("sentry")

# Update individual sink
current_config.set_level("sentry", "NONE")
set_pii_config(current_config)
```

### Custom Exception Handling

The DRF exception handler can be customized:

```python
from observe_kit.drf import observed_exception_handler
from rest_framework.views import exception_handler

def custom_exception_handler(exc, context):
    # Your custom logic
    response = observed_exception_handler(exc, context)
    # Additional processing
    return response

# In settings.py
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "myapp.handlers.custom_exception_handler",
}
```

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details on:

- Setting up your development environment
- Code style and testing requirements
- Pull request process

Before contributing, please read our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
