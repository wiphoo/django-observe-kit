# Django Core Example

Goal: verify observe_kit in a plain Django app without Django REST Framework.

This example shows:

- request-scoped context
- `X-Trace-Id` response headers
- structured request logging
- Prometheus `/metrics`

## Run

```bash
cd examples/django-core
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

Send a request:

```bash
curl -i http://127.0.0.1:8000/
curl http://127.0.0.1:8000/metrics
```

## What To Verify

- Responses include `X-Trace-Id`
- Logs include `request_complete`
- `/metrics` includes `http_requests_total`

## Tests

```bash
uv run pytest
```
