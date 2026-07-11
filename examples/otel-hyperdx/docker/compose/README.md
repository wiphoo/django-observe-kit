# Local HyperDX Demo Stack

This stack visualizes the OTEL example app's traces and logs in HyperDX.

## Services

- `otel-collector`: receives OTLP telemetry on `localhost:4318`
- `clickhouse`: stores traces and logs for HyperDX
- `mongodb`: session and metadata storage for HyperDX
- `hyperdx`: UI on `http://localhost:8080`

## Start

```bash
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
```

## Run The Django App

Start the app on the host with OTLP enabled:

```bash
OBSERVE_KIT_OTEL_ENDPOINT=http://127.0.0.1:4318 \
OBSERVE_KIT_SERVICE_NAME=example-otel-hyperdx \
OBSERVE_KIT_ENABLE_CONSOLE_SPANS=0 \
uv run python src/manage.py runserver
```

## Generate Demo Telemetry

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

Open `http://localhost:8080` and log in with the credentials from `docker/compose/.env`.

## Stop

```bash
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env down
```
