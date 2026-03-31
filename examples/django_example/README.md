# Django Example

This example demonstrates basic Django integration with observe_kit.

## Setup

1. Install dependencies:
```bash
pip install -e ../.. Django
```

2. Configure database (SQLite by default):
```bash
python manage.py migrate
```

3. Run the server:
```bash
python manage.py runserver
```

## Features Demonstrated

- Request context middleware
- JSON structured logging
- Health check endpoint
- Prometheus metrics endpoint
- Basic view observability

## Endpoints

- `http://localhost:8000/` - Main page
- `http://localhost:8000/healthz` - Health check
- `http://localhost:8000/healthz/detailed` - Detailed health check
- `http://localhost:8000/metrics` - Prometheus metrics

## Configuration

See `settings.py` for:
- Middleware configuration
- Logging setup
- observe_kit configuration





