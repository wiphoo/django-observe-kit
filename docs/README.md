# Documentation

Django Observe Kit wires request context, structured logging, OpenTelemetry
tracing, Prometheus metrics, Sentry enrichment, and audit logging into Django,
DRF, and Wagtail — with PII-aware defaults.

## Guides

- **[Configuration Reference](configuration.md)** — every `OBSERVE_KIT` setting,
  loaded via `observe_kit.settings.get_observe_kit_settings()`.
- **[Middleware Reference](middleware.md)** — the middleware stack, required
  ordering, and the shared `RequestContext`.
- **[PII Sanitization](pii.md)** — how headers, query params, and bodies are
  sanitized before reaching logs, spans, Sentry, and the audit log.
- **[HyperDX + ClickHouse Quickstart](HYPERDX_QUICKSTART.md)** — get traces and
  logs flowing into HyperDX in about 5 minutes.

## Feature overview

- **Context & tracing** — request-scoped `contextvars`, W3C trace-context
  propagation (opt-in from untrusted edges), and an `X-Trace-Id` response header.
- **Per-sink PII control** — `PiiConfig` sets independent sanitization levels for
  logs, OTEL, Sentry, and the audit sink.
- **DRF integration** — automatic ViewSet action detection and span renaming to
  `drf.<ViewSet>.<action>`.
- **Metrics & health** — Prometheus counters with label-cardinality caps,
  optional DB-query tracking, and detailed health endpoints.
- **Audit & Sentry** — audit entries carrying trace IDs and Sentry error
  enrichment.

## Runnable examples

Each subdirectory under [`examples/`](../examples) is a standalone `uv` project
demonstrating one feature area (`django-core`, `drf-observability`,
`otel-hyperdx`, `grafana-metrics`, `metrics-security`, `sentry`,
`pii-sanitization`, `audit-logs`, `tenant-trace-security`,
`wagtail-observability`). See [`examples/README.md`](../examples/README.md).

## Contributing & releases

- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — development workflow and test commands.
- [`CHANGELOG.md`](../CHANGELOG.md) — user-facing release notes.
- [`SECURITY.md`](../SECURITY.md) — how to report a vulnerability.
