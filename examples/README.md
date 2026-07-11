# Example Django Observe Kit

This repo now ships separate runnable examples so each `observe_kit` use case is easy to read and verify on its own.

## Choose An Example

### OTEL + HyperDX

Use this when you want to learn request tracing, child spans, and correlated logs with a local OTLP stack.

Path: `examples/otel-hyperdx`

```bash
cd examples/otel-hyperdx
uv sync
```

### Django Core

Use this when you want the smallest plain Django example for request context, trace headers, structured request logs, and `/metrics`.

Path: `examples/django-core`

```bash
cd examples/django-core
uv sync
```

### DRF Observability

Use this when you want to verify Django REST Framework ViewSet action naming and the observed exception handler.

Path: `examples/drf-observability`

```bash
cd examples/drf-observability
uv sync
```

### Grafana Metrics

Use this when you want Prometheus-style Django metrics that Grafana can visualize.

Path: `examples/grafana-metrics`

```bash
cd examples/grafana-metrics
uv sync
```

### Metrics Security

Use this when you want to verify `/metrics` access control with token and staff-only modes.

Path: `examples/metrics-security`

```bash
cd examples/metrics-security
uv sync
```

### Sentry

Use this when you want to verify error capture and request context in Sentry.

Path: `examples/sentry`

```bash
cd examples/sentry
uv sync
```

### PII Sanitization

Use this when you want to verify per-sink PII levels, extra mask/hash fields, and sanitized audit payloads.

Path: `examples/pii-sanitization`

```bash
cd examples/pii-sanitization
uv sync
```

### Audit Logs

Use this when you want to record and inspect business audit events with trace correlation.

Path: `examples/audit-logs`

```bash
cd examples/audit-logs
uv sync
```

### Tenant + Trace Security

Use this when you want to verify tenant extraction, trusted proxies, inbound trace-context trust, and metrics label cardinality overflow.

Path: `examples/tenant-trace-security`

```bash
cd examples/tenant-trace-security
uv sync
```

### Wagtail Observability

Use this when you want to verify `observe_kit` across a real Wagtail CMS workflow, including admin traffic, public page delivery, metrics, and OTEL traces/logs.

Path: `examples/wagtail-observability`

```bash
cd examples/wagtail-observability
uv sync
```

## Shared Notes

- All examples use Python 3.12 and Django 4.x.
- The API-focused examples use Django REST Framework.
- Examples that need local dependency services include a `docker/compose/` directory.
- Each example has its own `README.md` with focused setup steps and verification points.
- The quote-based examples use the same request payload so the only thing that changes is the observability behavior you are inspecting.
