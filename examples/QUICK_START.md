# Quick Start Guide

This guide will help you quickly set up and run the observe_kit examples.

## Prerequisites

- Python 3.9+
- pip or uv

## Installation

1. **Install observe_kit in development mode**:
```bash
cd /path/to/Django-Observe_Kit
pip install -e .[dev]
```

2. **Navigate to an example**:
```bash
cd examples/django_example  # or drf_example or wagtail_example
```

3. **Install example-specific dependencies**:
```bash
# For Django example
pip install Django

# For DRF example
pip install Django djangorestframework

# For Wagtail example
pip install Django wagtail
```

## Running Examples

### Django Example

```bash
cd examples/django_example
python manage.py migrate
python manage.py runserver
```

**Test it**:
```bash
# Visit main page
curl http://localhost:8000/

# Check health
curl http://localhost:8000/healthz

# View metrics
curl http://localhost:8000/metrics
```

**What to observe**:
- JSON logs in console with request context
- Trace IDs in responses (X-Trace-Id header)
- Metrics at `/metrics` endpoint

### DRF Example

```bash
cd examples/drf_example
python manage.py migrate
python manage.py runserver
```

**Test it**:
```bash
# List users (automatic DRF detection)
curl http://localhost:8000/api/users/

# Create a user
curl -X POST http://localhost:8000/api/users/ \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "email": "test@example.com"}'
```

**What to observe**:
- Spans named as `drf.UserViewSet.list`, `drf.UserViewSet.create`
- Structured logs with DRF route information
- Trace IDs in API responses

### Wagtail Example

```bash
cd examples/wagtail_example
python manage.py migrate
python manage.py createsuperuser  # Create admin user
python manage.py runserver
```

**Test it**:
1. Visit http://localhost:8000/admin/ (login)
2. Create/edit a page
3. Publish the page
4. Check logs for `wagtail_publish` events

**What to observe**:
- Admin requests tagged with `framework="wagtail_admin"`
- Wagtail metrics: `wagtail_pages_published_total`
- Audit logs for page operations

### Wagtail Heroku Example

```bash
cd examples/wagtail_heroku_example
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

**What to observe**:
- `/healthz` for uptime monitors
- `/metrics` with optional basic auth
- Env-driven Sentry / HyperDX / Neon config
- Ready-to-build Docker image for Heroku:
  ```bash
  docker build -t wagtail-heroku-example -f examples/wagtail_heroku_example/Dockerfile .
  ```

## Enabling Optional Features

### OpenTelemetry Tracing

Uncomment in `settings.py`:
```python
from observe_kit.otel import init_tracing
init_tracing(service_name="my-service", endpoint="http://localhost:4318")
```

### Sentry Error Tracking

Uncomment in `settings.py`:
```python
from observe_kit.sentry import init_sentry
init_sentry(
    dsn="https://your-key@o0.ingest.sentry.io/your-project",
    environment="development",
)
```

### Custom PII Configuration

Modify in `settings.py`:
```python
configure_logging(
    level="INFO",
    pii_levels={
        "logs": "BASIC",      # Basic sanitization for logs
        "otel": "BASIC",      # Basic for traces
        "sentry": "SENSITIVE",  # More restrictive for errors
        "audit": "NONE",      # No sanitization for compliance
    },
)
```

## Understanding the Output

### JSON Logs

All logs are in JSON format with request context:
```json
{
  "level": "INFO",
  "logger": "observe_kit.request",
  "message": "request_complete",
  "method": "GET",
  "path": "/api/users/",
  "route": "drf.UserViewSet.list",
  "status": 200,
  "duration_ms": 45.2,
  "trace_id": "abc123...",
  "tenant_id": "tenant1",
  "db_queries": 3,
  "db_time_ms": 12.5
}
```

### Metrics

Prometheus metrics at `/metrics`:
```
http_requests_total{method="GET",route="drf.UserViewSet.list",status="200",tenant="tenant1"} 10
http_request_duration_seconds{method="GET",route="drf.UserViewSet.list",status="200",tenant="tenant1"} 0.045
db_queries_per_request{route="drf.UserViewSet.list",tenant="tenant1"} 3.0
```

### Trace IDs

Every response includes `X-Trace-Id` header for correlation:
```bash
curl -I http://localhost:8000/api/users/
# X-Trace-Id: abc123def456...
```

## Next Steps

1. **Review the code**: Check `views.py` in each example to see usage patterns
2. **Customize**: Modify settings to match your needs
3. **Integrate**: Use these examples as templates for your project
4. **Monitor**: Set up Prometheus/Grafana for metrics visualization
5. **Trace**: Configure OTEL collector for distributed tracing

## Troubleshooting

### Import Errors
```bash
# Make sure observe_kit is installed
pip install -e ../..
```

### Database Errors
```bash
# Run migrations
python manage.py migrate
```

### Missing Dependencies
```bash
# Install all example dependencies
pip install Django djangorestframework wagtail
```

---

For more details, see individual example README files.




