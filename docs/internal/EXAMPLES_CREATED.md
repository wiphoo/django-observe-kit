# Examples Directory - Implementation Complete ✅

## Summary

Comprehensive example projects have been created demonstrating observe_kit integration with Django, DRF, and Wagtail.

## Statistics

- **Total Files**: 32 files
- **Python Files**: 27 files
- **Documentation Files**: 5 markdown files
- **Example Projects**: 3 complete projects

## Example Projects Created

### 1. Django Example (`django_example/`)

**Purpose**: Basic Django integration

**Files Created**:
- `settings.py` - Complete middleware and app configuration
- `urls.py` - URL routing with observe_kit endpoints
- `manage.py` - Django management script
- `wsgi.py` - WSGI configuration
- `example_app/views.py` - Example views using observe_kit
- `example_app/apps.py` - App configuration
- `example_app/models.py` - Example models
- `README.md` - Setup and usage instructions

**Features Demonstrated**:
- ✅ Middleware configuration
- ✅ JSON structured logging
- ✅ Health check endpoints
- ✅ Prometheus metrics
- ✅ Request context tracking
- ✅ Audit logging

### 2. DRF Example (`drf_example/`)

**Purpose**: Django REST Framework integration

**Files Created**:
- `settings.py` - DRF + observe_kit configuration
- `urls.py` - DRF router setup
- `manage.py` - Django management script
- `wsgi.py` - WSGI configuration
- `api_app/views.py` - ViewSets with observability
- `api_app/models.py` - Example Post model
- `api_app/serializers.py` - DRF serializers
- `api_app/apps.py` - App configuration
- `README.md` - Setup and usage instructions

**Features Demonstrated**:
- ✅ DRF middleware for automatic ViewSet detection
- ✅ DRF exception handler
- ✅ Automatic span naming: `drf.<ViewSet>.<action>`
- ✅ API observability
- ✅ Custom ViewSet actions
- ✅ Audit logging in ViewSets

### 3. Wagtail Example (`wagtail_example/`)

**Purpose**: Wagtail CMS integration

**Files Created**:
- `settings.py` - Wagtail + observe_kit configuration
- `urls.py` - Wagtail URL routing
- `manage.py` - Django management script
- `wsgi.py` - WSGI configuration
- `cms_app/models.py` - Wagtail page models
- `cms_app/apps.py` - App configuration
- `README.md` - Setup and usage instructions

**Features Demonstrated**:
- ✅ Wagtail hooks integration (publish/unpublish/delete)
- ✅ Admin request tagging (`framework="wagtail_admin"`)
- ✅ Page observability
- ✅ Wagtail-specific metrics
- ✅ Audit logs for page operations

## Documentation Files

### Main Documentation
- `examples/README.md` - Overview of all examples
- `examples/QUICK_START.md` - Quick setup guide
- `examples/EXAMPLES_SUMMARY.md` - Detailed summary of each example

### Example-Specific READMEs
- `django_example/README.md` - Django example instructions
- `drf_example/README.md` - DRF example instructions
- `wagtail_example/README.md` - Wagtail example instructions

## Key Features Demonstrated

### Common to All Examples

1. **Middleware Configuration**
   - Proper middleware order
   - All observe_kit middleware included
   - Framework-specific middleware (DRF, Wagtail)

2. **Logging Setup**
   - JSON structured logging
   - Per-sink PII configuration
   - Request context injection

3. **Health & Metrics**
   - Health check endpoints
   - Detailed health checks
   - Prometheus metrics

4. **Request Context Usage**
   - Accessing request context
   - Using trace IDs
   - Tenant ID tracking

5. **Audit Logging**
   - Using audit() helper
   - Tracking user actions
   - Object-level auditing

### Framework-Specific Features

#### Django Example
- Basic view integration
- Request context in views
- Simple audit logging

#### DRF Example
- Automatic ViewSet action detection
- DRF exception handler
- Custom ViewSet actions
- Span naming: `drf.UserViewSet.list`

#### Wagtail Example
- Admin request tagging
- Wagtail hooks integration
- Page publish/unpublish/delete tracking
- Wagtail-specific metrics

## File Structure

```
examples/
├── README.md                    # Overview
├── QUICK_START.md               # Quick setup
├── EXAMPLES_SUMMARY.md          # Detailed summary
├── django_example/
│   ├── README.md
│   ├── settings.py
│   ├── urls.py
│   ├── manage.py
│   ├── wsgi.py
│   └── example_app/
│       ├── views.py
│       ├── models.py
│       ├── apps.py
│       └── migrations/
├── drf_example/
│   ├── README.md
│   ├── settings.py
│   ├── urls.py
│   ├── manage.py
│   ├── wsgi.py
│   └── api_app/
│       ├── views.py
│       ├── models.py
│       ├── serializers.py
│       ├── apps.py
│       └── migrations/
└── wagtail_example/
    ├── README.md
    ├── settings.py
    ├── urls.py
    ├── manage.py
    ├── wsgi.py
    └── cms_app/
        ├── models.py
        ├── apps.py
        └── migrations/
```

## Usage

### Quick Start

1. **Install observe_kit**:
```bash
cd /path/to/Django-Observe_Kit
pip install -e .[dev]
```

2. **Choose an example**:
```bash
cd examples/django_example  # or drf_example or wagtail_example
```

3. **Install dependencies**:
```bash
pip install Django  # or Django djangorestframework or Django wagtail
```

4. **Run migrations**:
```bash
python manage.py migrate
```

5. **Run server**:
```bash
python manage.py runserver
```

### Testing Examples

#### Django Example
```bash
curl http://localhost:8000/
curl http://localhost:8000/healthz
curl http://localhost:8000/metrics
```

#### DRF Example
```bash
curl http://localhost:8000/api/users/
curl -X POST http://localhost:8000/api/users/ \
  -H "Content-Type: application/json" \
  -d '{"username": "test", "email": "test@example.com"}'
```

#### Wagtail Example
1. Visit http://localhost:8000/admin/
2. Create/edit a page
3. Publish the page
4. Check logs for Wagtail events

## Integration Points

### Middleware Order
All examples demonstrate the correct middleware order:
1. TraceContextMiddleware
2. RequestContextMiddleware
3. UserLoggingContextMiddleware
4. DRFIntegrationMiddleware (DRF only)
5. RequestLoggingMiddleware
6. PrometheusRequestMiddleware
7. SentryContextMiddleware

### Configuration Patterns
- Logging configuration with per-sink PII levels
- Optional OTEL tracing setup
- Optional Sentry setup
- Health and metrics endpoints

### Code Patterns
- Using `get_request_context()` in views
- Using `audit()` helper for audit logging
- Accessing trace IDs and tenant IDs
- Structured logging with context

## Benefits

1. **Learning**: Clear examples of how to integrate observe_kit
2. **Reference**: Copy-paste ready configuration
3. **Testing**: Test observe_kit features in isolation
4. **Documentation**: Living examples of best practices
5. **Onboarding**: Quick start for new users

## Next Steps

1. **Run an example**: Choose one and follow its README
2. **Experiment**: Modify settings and see the effects
3. **Integrate**: Use as a template for your project
4. **Extend**: Add your own views/models and observe them

---

**Status**: ✅ Complete
**Date**: 2024-11-29
**Files**: 32 total (27 Python, 5 Markdown)
**Examples**: 3 complete projects



