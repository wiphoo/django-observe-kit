# Examples Summary

This directory contains three complete, working examples demonstrating observe_kit integration with Django, DRF, and Wagtail.

## Example Projects

### 1. Django Example (`django_example/`)

**Purpose**: Basic Django integration

**Features Demonstrated**:
- Middleware configuration
- JSON structured logging
- Health check endpoints
- Prometheus metrics
- Basic view observability
- Request context tracking

**Files**:
- `settings.py` - Complete middleware setup
- `urls.py` - URL routing with observe_kit endpoints
- `example_app/views.py` - Example views using observe_kit
- `manage.py` - Django management script

**Run**:
```bash
cd django_example
python manage.py migrate
python manage.py runserver
```

**Endpoints**:
- `http://localhost:8000/` - Main page
- `http://localhost:8000/healthz` - Health check
- `http://localhost:8000/metrics` - Prometheus metrics

---

### 2. DRF Example (`drf_example/`)

**Purpose**: Django REST Framework integration

**Features Demonstrated**:
- DRF middleware for automatic ViewSet detection
- DRF exception handler
- API observability
- Automatic span naming: `drf.<ViewSet>.<action>`
- Audit logging in ViewSets

**Files**:
- `settings.py` - DRF + observe_kit configuration
- `urls.py` - DRF router setup
- `api_app/views.py` - ViewSets with observability
- `api_app/models.py` - Example models
- `api_app/serializers.py` - DRF serializers

**Run**:
```bash
cd drf_example
python manage.py migrate
python manage.py runserver
```

**Endpoints**:
- `http://localhost:8000/api/users/` - User ViewSet
- `http://localhost:8000/api/posts/` - Post ViewSet
- `http://localhost:8000/healthz` - Health check
- `http://localhost:8000/metrics` - Prometheus metrics

**Key Features**:
- Automatic detection of ViewSet actions (list, create, retrieve, update, destroy)
- Custom actions detected as `drf.UserViewSet.activate`
- Exception handling with PII-safe logging
- Trace IDs in API responses

---

### 3. Wagtail Example (`wagtail_example/`)

**Purpose**: Wagtail CMS integration

**Features Demonstrated**:
- Wagtail hooks integration (publish/unpublish/delete)
- Admin request tagging (`framework="wagtail_admin"`)
- Page observability
- Wagtail-specific metrics

**Files**:
- `settings.py` - Wagtail + observe_kit configuration
- `urls.py` - Wagtail URL routing
- `cms_app/models.py` - Wagtail page models

**Run**:
```bash
cd wagtail_example
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

**Endpoints**:
- `http://localhost:8000/admin/` - Wagtail admin (tagged as wagtail_admin)
- `http://localhost:8000/` - Public site
- `http://localhost:8000/healthz` - Health check
- `http://localhost:8000/metrics` - Prometheus metrics (includes Wagtail metrics)

**Key Features**:
- Admin requests automatically tagged with `framework="wagtail_admin"`
- Page publish/unpublish/delete events tracked
- Wagtail metrics:
  - `wagtail_pages_published_total{tenant}`
  - `wagtail_pages_unpublished_total{tenant}`
  - `wagtail_pages_deleted_total{tenant}`
- Audit logs for all page operations

---

### 4. Wagtail Heroku Example (`wagtail_heroku_example/`)

**Purpose**: Deploy observe_kit + Wagtail to Heroku Container stack

**Features Demonstrated**:
- Dockerfile + `heroku.yml` for container deploys
- Neon Postgres integration via `dj-database-url`
- Sentry + HyperDX configuration via env vars
- Prometheus metrics endpoint with optional basic auth
- Health endpoint for uptime checks

**Files**:
- `Dockerfile` / `heroku.yml` - container + deploy definition
- `settings.py` - env-driven config (DB, Sentry, OTEL, logging)
- `entrypoint.sh` - runs migrations + gunicorn
- `urls.py` - metrics auth + health check

**Run locally**:
```bash
cd examples/wagtail_heroku_example
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

**Docker**:
```bash
docker build -t wagtail-heroku-example -f examples/wagtail_heroku_example/Dockerfile .
docker run --rm -p 8000:8000 wagtail-heroku-example
```

---

## Common Patterns

All examples demonstrate:

### 1. Middleware Configuration

```python
MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    # ... Django middleware
]
```

### 2. Logging Configuration

```python
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
```

### 3. Using Request Context

```python
from observe_kit.context import get_request_context

def my_view(request):
    context = get_request_context()
    logger.info("my_event", extra={
        "trace_id": context.trace_id,
        "tenant_id": context.tenant_id,
        "db_queries": context.db_queries,
    })
```

### 4. Audit Logging

```python
from observe_kit.audit import audit

audit(
    actor=request.user,
    action="create_item",
    obj=item,
    request=request,
)
```

## File Structure

```
examples/
├── README.md                    # Overview
├── QUICK_START.md               # Quick setup guide
├── EXAMPLES_SUMMARY.md          # This file
├── django_example/
│   ├── README.md
│   ├── settings.py
│   ├── urls.py
│   ├── manage.py
│   └── example_app/
│       ├── views.py
│       └── ...
├── drf_example/
│   ├── README.md
│   ├── settings.py
│   ├── urls.py
│   ├── manage.py
│   └── api_app/
│       ├── views.py
│       ├── models.py
│       └── ...
└── wagtail_example/
    ├── README.md
    ├── settings.py
    ├── urls.py
    ├── manage.py
    └── cms_app/
        ├── models.py
        └── ...
```

## Testing the Examples

### Django Example
```bash
curl http://localhost:8000/
curl http://localhost:8000/healthz
curl http://localhost:8000/metrics
```

### DRF Example
```bash
curl http://localhost:8000/api/users/
curl -X POST http://localhost:8000/api/users/ \
  -H "Content-Type: application/json" \
  -d '{"username": "test", "email": "test@example.com"}'
```

### Wagtail Example
1. Visit http://localhost:8000/admin/
2. Create/edit a page
3. Publish the page
4. Check logs for Wagtail events

## What to Observe

### JSON Logs
All examples produce structured JSON logs:
```json
{
  "level": "INFO",
  "message": "request_complete",
  "method": "GET",
  "path": "/api/users/",
  "route": "drf.UserViewSet.list",
  "status": 200,
  "trace_id": "abc123...",
  "tenant_id": "tenant1",
  "db_queries": 3,
  "db_time_ms": 12.5
}
```

### Metrics
Prometheus metrics at `/metrics`:
```
http_requests_total{method="GET",route="drf.UserViewSet.list",status="200"} 10
http_request_duration_seconds{method="GET",route="drf.UserViewSet.list",status="200"} 0.045
db_queries_per_request{route="drf.UserViewSet.list"} 3.0
```

### Trace IDs
Every response includes `X-Trace-Id` header for correlation.

## Next Steps

1. **Run an example**: Choose one and follow its README
2. **Modify it**: Experiment with different configurations
3. **Integrate**: Use as a template for your project
4. **Extend**: Add your own views/models and observe them

---

For detailed instructions, see individual example README files or `QUICK_START.md`.



