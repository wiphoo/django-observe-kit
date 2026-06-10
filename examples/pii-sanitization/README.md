# PII Sanitization Example

Goal: verify per-sink PII controls for request context and audit logs.

This example shows:

- `PII_LEVELS` per sink
- `PII_HASH_SALT`
- extra mask and hash fields
- sanitized audit payloads

## Run

```bash
cd examples/pii-sanitization
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

Send a request:

```bash
curl -X POST 'http://127.0.0.1:8000/api/privacy/submit/?email=alice@example.com' \
  -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","phone":"5551234567","ssn":"123-45-6789","session_id":"session-123"}'
```

## What To Verify

- Query param `email` is masked in request context.
- Audit payload masks `email`, `phone`, and `ssn`.
- Audit payload hashes `session_id`.

## Tests

```bash
uv run pytest
```
