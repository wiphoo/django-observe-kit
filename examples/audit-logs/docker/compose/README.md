# Local Audit Logs Stack

This stack provides PostgreSQL and Adminer for the audit-logs example.

## Services

- `postgres`: stores audit log rows for the demo
- `adminer`: lightweight SQL UI for direct verification

## Start

```bash
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
export $(grep -v '^#' docker/compose/.env | xargs)
```

## Run The Django App

```bash
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

## Verify

1. Send the quote request.
2. Confirm the row is returned by `GET /api/audit/`.
3. Open Adminer at `http://localhost:8081` and inspect the `observe_kit_audit_auditlog` table.

## Stop

```bash
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env down
```
