# Tenant and Trace Security Example

Goal: verify tenant extraction and inbound W3C trace-context trust controls.

This example shows:

- tenant resolution from `X-Tenant-Id`
- tenant resolution from subdomains
- default rejection of inbound `traceparent`
- trusted-proxy and trusted-source trace propagation
- Prometheus label cardinality overflow

## Run

```bash
cd examples/tenant-trace-security
uv sync
uv run python src/manage.py migrate
uv run python src/manage.py runserver
```

## Tests

```bash
uv run pytest
```
