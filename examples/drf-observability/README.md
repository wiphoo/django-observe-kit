# DRF Observability Example

Goal: verify observe_kit's Django REST Framework integration.

This example shows:

- ViewSet action route names such as `drf.QuoteViewSet.quote`
- `observed_exception_handler` for server errors
- Sentry capture for 5xx exceptions
- request trace IDs in API responses

## Run

```bash
cd examples/drf-observability
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

## Try It

```bash
curl -X POST http://127.0.0.1:8000/api/quotes/quote/ \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"cust-drf","items":[{"sku":"sku-1","quantity":1,"unit_price":"10.00"}]}'

curl http://127.0.0.1:8000/api/quotes/failure/
```

## Tests

```bash
uv run pytest
```
