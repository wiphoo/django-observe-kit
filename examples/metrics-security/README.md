# Metrics Security Example

Goal: verify access control for observe_kit's Prometheus `/metrics` endpoint.

This example shows:

- unauthenticated development metrics
- token-protected metrics
- staff-only metrics
- production warning when metrics are exposed without auth

## Run

```bash
cd examples/metrics-security
uv sync
uv run python src/manage.py migrate
OBSERVE_KIT_METRICS_AUTH=token OBSERVE_KIT_METRICS_TOKEN=dev-token \
  uv run python src/manage.py runserver
```

Scrape with a token:

```bash
curl -H 'Authorization: Bearer dev-token' http://127.0.0.1:8000/metrics
```

## Tests

```bash
uv run pytest
```
