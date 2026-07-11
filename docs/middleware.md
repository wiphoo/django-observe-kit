# Middleware Reference

Observe Kit ships several middlewares that share state via a single `RequestContext` stored in a `ContextVar`. They must be registered in a specific order because each step has dependencies on what earlier steps wrote to the context — and because Django runs `process_response` **bottom-up**.

## Canonical order

```python
MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",
    "observe_kit.logging.middleware.RequestLoggingMiddleware",
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
    "observe_kit.context_middleware.RequestContextMiddleware",
    "observe_kit.context_middleware.UserLoggingContextMiddleware",
    # "observe_kit.drf.integration.DRFIntegrationMiddleware",  # optional, when using DRF
    "observe_kit.sentry.middleware.SentryContextMiddleware",
    # ...rest of your Django/DRF/Wagtail middleware...
]
```

This is the order used by every app in `examples/` and by `docs/HYPERDX_QUICKSTART.md`. Treat it as authoritative.

## Why this order

Django middleware semantics:

- `process_request` runs **top-to-bottom**.
- `process_view` runs **top-to-bottom** for each registered middleware that defines it, after URL resolution.
- `process_exception` runs **bottom-to-top** when a view raises.
- `process_response` runs **bottom-to-top**.

`RequestContextMiddleware.process_response` is the step that finalizes `status`, `duration_ms`, `db_queries`, and `db_time_ms` on the shared context. It must run **before** `RequestLoggingMiddleware` and `PrometheusRequestMiddleware` read those fields. Because response phase runs bottom-up, `RequestContextMiddleware` is placed *below* logging and metrics on purpose — that puts its `process_response` *earlier* in the response chain.

## Chain table

| # | Middleware | `process_request` | `process_view` | `process_response` | Reads from context | Writes to context |
|---|---|---|---|---|---|---|
| 1 | `TraceContextMiddleware` | Extracts W3C `traceparent`, starts a `SERVER` span, sets HTTP attributes, stores span on request. | — | Sets `http.response.status_code`, span status, adds `X-Trace-Id` response header, ends span, resets context. | `route` (from later DRF detection) | `trace_id`, `span_id` |
| 2 | `RequestLoggingMiddleware` | — | — | Emits the JSON `request_complete` log via `observe_kit.request`. | `status` | — |
| 3 | `PrometheusRequestMiddleware` | — | — | Records HTTP & DB Prometheus metrics for the request. | `method`, `route`, `path`, `status`, `duration_ms`, `tenant_id`, `db_queries`, `db_time_ms` | — |
| 4 | `RequestContextMiddleware` | Builds a fresh `RequestContext` (method, path, sanitized headers/query params, user, tenant, framework). Starts the request timer and optional `QueryRecorder`. | Populates `route` from `resolver_match`. | Finalizes `status`, `duration_ms`, `db_queries`, `db_time_ms`. Removes DB wrappers. | — | All the above |
| 5 | `UserLoggingContextMiddleware` | Re-binds the `ContextVar` to the per-request context so log filters see it. | — | — | — | — |
| 6 | `DRFIntegrationMiddleware` *(optional)* | — | Detects DRF ViewSet action and rewrites span name to `drf.<ViewSet>.<action>`. | — | — | `route` |
| 7 | `SentryContextMiddleware` | Attaches `otel.trace_id`, `tenant_id`, `http.method`, `http.path` to the current Sentry scope. | — | — | `trace_id`, `tenant_id`, `method`, `path` | — |

> Optional middlewares (DRF) only need to be installed when the relevant framework is in use. The library imports them lazily and skips work when the framework is missing.

## What runs when

### Request phase (top-to-bottom)

```
TraceContext         → start span, parse traceparent, set trace_id/span_id
RequestLogging       → (no-op on request)
PrometheusRequest    → (no-op on request)
RequestContext       → build RequestContext, start timer, wrap DB connections
UserLoggingContext   → re-bind ContextVar (so log filters see it)
DRFIntegration       → (no-op on request)
SentryContext        → attach trace/tenant/method/path to Sentry scope
```

### View phase

```
TraceContext         → (no process_view)
RequestContext       → set context.route from resolver_match
DRFIntegration       → if ViewSet, set context.route = "drf.<ViewSet>.<action>" and rename span
```

### Response phase (bottom-to-top)

```
SentryContext        → (no process_response)
DRFIntegration       → (no process_response)
UserLoggingContext   → (no process_response)
RequestContext       → finalize status / duration_ms / db_queries / db_time_ms, unwrap DB
PrometheusRequest    → emit metrics using the now-finalized context
RequestLogging       → emit "request_complete" JSON log
TraceContext         → set HTTP status on span, write X-Trace-Id header, end span, reset context
```

## Failure handling

Every middleware wraps its logic in `try/except`. A failure inside any single middleware logs a warning and lets the request continue. `RequestContextMiddleware.process_exception` and `process_response` always remove the DB connection wrappers so an exception cannot leak instrumentation between requests.

## Related

- [`docs/configuration.md`](configuration.md) — `OBSERVE_KIT` settings reference
- [`docs/pii.md`](pii.md) — what `RequestContextMiddleware` sanitizes from headers and query params
- [`docs/HYPERDX_QUICKSTART.md`](HYPERDX_QUICKSTART.md) — end-to-end onboarding
- `examples/*/settings.py` — runnable apps using this exact order
