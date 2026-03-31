# DRF Example

This example demonstrates Django REST Framework integration with observe_kit.

## Setup

1. Install dependencies:
```bash
pip install -e ../.. Django djangorestframework
```

2. Configure database:
```bash
python manage.py migrate
```

3. Create a superuser (optional):
```bash
python manage.py createsuperuser
```

4. Run the server:
```bash
python manage.py runserver
```

## Features Demonstrated

- DRF middleware for automatic ViewSet action detection
- DRF exception handler
- API observability
- Span naming: `drf.<ViewSet>.<action>`

## Endpoints

- `http://localhost:8000/api/users/` - User ViewSet (list, create)
- `http://localhost:8000/api/users/{id}/` - User ViewSet (retrieve, update, delete)
- `http://localhost:8000/api/posts/` - Post ViewSet
- `http://localhost:8000/healthz` - Health check
- `http://localhost:8000/metrics` - Prometheus metrics

## Key Features

1. **Automatic ViewSet Detection**: DRFIntegrationMiddleware automatically detects ViewSet actions
2. **Span Naming**: Spans are named as `drf.UserViewSet.list`, `drf.UserViewSet.create`, etc.
3. **Exception Handling**: DRF exceptions are handled with PII-safe logging
4. **Metrics**: All API requests are tracked with route information

## Testing the Integration

```bash
# List users
curl http://localhost:8000/api/users/

# Create a user
curl -X POST http://localhost:8000/api/users/ \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "email": "test@example.com"}'

# Get specific user
curl http://localhost:8000/api/users/1/
```

Check the logs to see:
- DRF route detection: `drf.UserViewSet.list`
- Trace IDs in responses
- Structured JSON logs with request context





