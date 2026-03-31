# Observe Kit Examples

This directory contains example projects demonstrating how to use observe_kit with Django, DRF, and Wagtail.

## Examples

### 1. Django Example (`django_example/`)
Basic Django project showing:
- Middleware configuration
- Logging setup
- Basic view integration
- Health and metrics endpoints

### 2. DRF Example (`drf_example/`)
Django REST Framework integration showing:
- DRF middleware setup
- ViewSet action detection
- Exception handling
- API observability

### 3. Wagtail Example (`wagtail_example/`)
Wagtail CMS integration showing:
- Wagtail hooks integration
- Admin request tagging
- Page publish/unpublish observability

### 4. Wagtail Heroku Example (`wagtail_heroku_example/`)
Container-focused Wagtail deployment for Heroku:
- Dockerfile + `heroku.yml`
- Neon Postgres via `DATABASE_URL`
- HyperDX OTLP tracing/logging
- Sentry integration
- Prometheus metrics with optional basic auth

## Running the Examples

### Prerequisites

```bash
# Install observe_kit in development mode
cd /path/to/Django-Observe_Kit
pip install -e .[dev]

# Or install from the examples directory
pip install -e ../..
```

### Django Example

```bash
cd django_example
python manage.py migrate
python manage.py runserver
```

Visit:
- http://localhost:8000/ - Main page
- http://localhost:8000/healthz - Health check
- http://localhost:8000/metrics - Prometheus metrics

### DRF Example

```bash
cd drf_example
python manage.py migrate
python manage.py runserver
```

Visit:
- http://localhost:8000/api/users/ - User API
- http://localhost:8000/api/posts/ - Post API
- Check logs for DRF ViewSet action detection

### Wagtail Example

```bash
cd wagtail_example
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Visit:
- http://localhost:8000/admin/ - Wagtail admin
- http://localhost:8000/ - Public site
- Check logs for Wagtail admin tagging

### Wagtail Heroku Example

```bash
cd wagtail_heroku_example
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Visit:
- http://localhost:8000/admin/ - Wagtail admin
- http://localhost:8000/healthz - Health check for Heroku
- http://localhost:8000/metrics - Prometheus metrics (protect with env var)

To build the container image that Heroku will run:

```bash
docker build -t wagtail-heroku-example -f examples/wagtail_heroku_example/Dockerfile .
```

## Configuration

Each example includes:
- `settings.py` - Complete middleware and app configuration
- `urls.py` - URL routing including observe_kit endpoints
- Example views/models - Demonstrating usage
- `README.md` - Example-specific instructions

## Observability Features Demonstrated

- ✅ Request context tracking
- ✅ JSON structured logging
- ✅ OTEL tracing (if configured)
- ✅ Prometheus metrics
- ✅ Sentry integration (if configured)
- ✅ Audit logging
- ✅ PII sanitization
- ✅ Multi-tenant awareness
- ✅ DRF ViewSet detection
- ✅ Wagtail admin tagging




