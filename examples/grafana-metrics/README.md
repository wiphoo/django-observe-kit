# Grafana Metrics Example

Goal: expose Prometheus-style metrics from Django so they can be scraped and visualized in Grafana.

## What This Example Includes

- `POST /api/quotes/quote/` to create application traffic
- `/metrics` for Prometheus-compatible metrics output
- A minimal request flow so the metrics stay easy to understand

## Quick Start

```bash
cd examples/grafana-metrics
uv sync
uv run python src/manage.py migrate
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
uv run python src/manage.py runserver
```

## Trigger The Demo

```bash
curl -X POST http://127.0.0.1:8000/api/quotes/quote/ \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-metrics",
    "items": [
      {"sku": "sku-1", "quantity": 1, "unit_price": "10.00"}
    ]
  }'
```

Then inspect the exported metrics:

```bash
curl http://127.0.0.1:8000/metrics
```

Then open the local dashboards:

- Grafana: `http://127.0.0.1:3000`
- Prometheus: `http://127.0.0.1:9090`

Login with the credentials from `docker/compose/.env`.

## What To Verify

- `http_requests_total` is present
- `http_request_duration_seconds` is present
- Repeating the quote request changes the counters and histograms
- Prometheus shows the `example-grafana-metrics` target as `UP`
- Grafana shows data on the preprovisioned dashboard

## Relevant Env Vars

- `OBSERVE_KIT_SERVICE_NAME`
- `OBSERVE_KIT_LOG_LEVEL`

## Tests

```bash
uv run pytest
```
