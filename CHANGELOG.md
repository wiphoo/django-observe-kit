# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Security
- **Breaking:** inbound W3C `traceparent` / `tracestate` headers are no longer extracted by default. Set `OBSERVE_KIT["TRUST_INCOMING_TRACE_CONTEXT"] = True` (mesh-internal services) or list trusted client IP / CIDR blocks in `OBSERVE_KIT["TRUSTED_TRACE_SOURCES"]` to restore propagation. Prevents trace-id poisoning, forced-sample DoS on the trace backend, and `tracestate` injection from untrusted edges. See [#4](https://github.com/wiphoo/Django-Observe_Kit/issues/4).
- Cap Prometheus `route` / `tenant` label cardinality at `OBSERVE_KIT["METRICS_MAX_LABEL_CARDINALITY"]` distinct values per process (default 1000; `0` disables). Stops attacker-controlled inputs — raw 404 paths, `X-Tenant-Id` headers, subdomain probes — from inflating Prometheus time-series count. See [#9](https://github.com/wiphoo/Django-Observe_Kit/issues/9).

### Fixed
- **Breaking-ish:** `PrometheusRequestMiddleware` no longer falls back to the raw request path when route resolution fails. Unresolved requests now collapse to `route="unknown"` instead of producing a new label series per probed URL. Dashboards that filtered on specific 404 paths will need to switch to `status="404"`. See [#9](https://github.com/wiphoo/Django-Observe_Kit/issues/9).

### Added
- `OBSERVE_KIT["METRICS_AUTH"]` setting (`"none"` | `"staff"` | `"token"`) and `OBSERVE_KIT["METRICS_TOKEN"]` to gate the Prometheus `/metrics` endpoint. Token mode uses constant-time comparison. When mode is `"none"` and Django `DEBUG` is `False`, a one-shot `RuntimeWarning` is emitted to flag the unauthenticated endpoint. See [#2](https://github.com/wiphoo/Django-Observe_Kit/issues/2).
- Startup validator that warns when `django.conf.settings.MIDDLEWARE` contains `observe_kit` middlewares in the wrong order or omits a required entry. Advisory only — never raises. Opt out via `OBSERVE_KIT["VALIDATE_MIDDLEWARE_ORDER"] = False`. See [#7](https://github.com/wiphoo/Django-Observe_Kit/issues/7).
- OTEL tracing with automatic W3C trace-context propagation plus the `X-Trace-Id` header.
- Per-sink PII configuration (`PiiConfig`) spanning logs, OTEL, Sentry, and audit sinks.
- DRF integration middleware that detects ViewSet actions and renames spans to `drf.<ViewSet>.<action>` while tagging frameworks.
- Structured logging, audit entries with trace IDs, request body sanitization, and optional DB-tracking controls.
- Health checks, validation helpers, and documentation that explain advanced usage and observability guardrails.

### Changed
- `configure_logging`, `init_tracing`, and `init_sentry` now validate inputs, honor per-sink PII, and document the new defaults.
- Middlewares gain graceful error handling, framework detection, and tighter span naming for DRF routes.
- Metrics, health checks, and Wagtail hooks now surface tenant/trace metadata consistently.

### Fixed
- Missing trace_id propagation in audit logs and logging handlers.
- DRF action detection gaps, Wagtail admin tagging, and middleware exceptions that previously halted requests.
- Misleading ValidationError documentation and coverage gaps around core middleware.

### Documentation
- README trimmed to the essentials with pointers to the public docs index (`docs/README.md`).
- CONTRIBUTING now focuses on the minimal workflow, and CHANGELOG highlights only user-facing releases.
- Public docs cover configuration, middleware, PII sanitization, and the HyperDX quickstart.
- Added `SECURITY.md` with a private vulnerability-reporting policy.
