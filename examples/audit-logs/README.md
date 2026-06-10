# Audit Logs Example

Goal: record an audit event for a business action and inspect the stored audit trail with trace correlation.

## What This Example Includes

- `POST /api/quotes/quote/` to create an auditable action
- `GET /api/audit/` to inspect stored audit records
- Trace IDs returned in the response so you can match the request to the audit log entry

## Quick Start

```bash
cd examples/audit-logs
uv sync
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
export $(grep -v '^#' docker/compose/.env | xargs)
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

## Trigger The Demo

```bash
curl -X POST http://127.0.0.1:8000/api/quotes/quote/ \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-audit",
    "items": [
      {"sku": "sku-1", "quantity": 1, "unit_price": "10.00"}
    ]
  }'
```

Then inspect the audit trail:

```bash
curl http://127.0.0.1:8000/api/audit/
```

You can also inspect the same records directly in the local database UI:

- Adminer: `http://127.0.0.1:8081`
- PostgreSQL: `127.0.0.1:5432`

## What To Verify

- A `quote_generated` audit record exists
- The audit record includes the expected `customer_id`
- The audit record trace ID matches the request trace ID returned by the API
- The same row is visible in PostgreSQL through Adminer

## Relevant Env Vars

- `OBSERVE_KIT_SERVICE_NAME`
- `OBSERVE_KIT_ENABLE_CONSOLE_SPANS`
- `OBSERVE_KIT_DB_ENGINE`
- `OBSERVE_KIT_DB_HOST`
- `OBSERVE_KIT_DB_PORT`
- `OBSERVE_KIT_DB_NAME`
- `OBSERVE_KIT_DB_USER`
- `OBSERVE_KIT_DB_PASSWORD`

## Tests

```bash
uv run pytest
```
