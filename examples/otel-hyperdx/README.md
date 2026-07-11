# OTEL + HyperDX Example

Goal: send Django request traces and correlated logs to an OTLP endpoint and inspect them in HyperDX.

## What This Example Includes

- `POST /api/quotes/quote/` to generate one request with nested spans
- Trace IDs returned in the response and `X-Trace-Id` header
- Structured logs tied to the active trace
- A local HyperDX + OTEL Collector Docker stack

## Quick Start

```bash
cd examples/otel-hyperdx
uv sync
uv run python src/manage.py migrate
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
OBSERVE_KIT_OTEL_ENDPOINT=http://127.0.0.1:4318 \
OBSERVE_KIT_SERVICE_NAME=example-otel-hyperdx \
OBSERVE_KIT_OTEL_SAMPLE_RATE=1.0 \
OBSERVE_KIT_ENABLE_CONSOLE_SPANS=0 \
uv run python src/manage.py runserver
```

## Trigger The Demo

```bash
curl -X POST http://127.0.0.1:8000/api/quotes/quote/ \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-123",
    "items": [
      {"sku": "sku-observe-kit", "quantity": 2, "unit_price": "19.50"},
      {"sku": "sku-drf", "quantity": 1, "unit_price": "9.00"}
    ]
  }'
```

## What To Verify

- The response contains `observability.trace_id`
- The response header `X-Trace-Id` is present
- HyperDX at `http://localhost:8080` shows the request trace and child spans
- HyperDX logs are searchable with the same trace ID

Login with the credentials from `docker/compose/.env`:

- Email: `admin@example.com`
- Password: `Admin123!@#$`

## Relevant Env Vars

- `OBSERVE_KIT_OTEL_ENDPOINT`
- `OBSERVE_KIT_SERVICE_NAME`
- `OBSERVE_KIT_OTEL_SAMPLE_RATE`
- `OBSERVE_KIT_ENABLE_CONSOLE_SPANS`

## Tests

```bash
uv run pytest
```
