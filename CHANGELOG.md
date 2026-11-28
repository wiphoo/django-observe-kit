# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.1.0] - 2026-11-28

First release.

### Features

- **Request context and structured logging:** request-scoped context and JSON logs, including a `request_complete` event. With `OBSERVE_KIT` set and `observe_kit` in `INSTALLED_APPS`, logging, tracing (when `SERVICE_NAME` is set) and Sentry (when `SENTRY_DSN` is set) are initialised at startup.
- **OpenTelemetry:** traces and log export over OTLP/HTTP, and an `X-Trace-Id` response header.
- **Prometheus:** HTTP request count and duration, database queries and time per request, audit and Wagtail page counters, and a `/metrics` view.
- **Sentry:** `init_sentry()` with PII scrubbing of error and transaction events, and request/trace/tenant context.
- **DRF:** ViewSet action detection, span renaming, and an exception handler.
- **Audit log:** the optional `observe_kit.audit` app stores audit entries with trace and tenant IDs, records logins and logouts, and blocks updates and deletes through the ORM and the admin (not through direct SQL).
- **Wagtail (optional, `[wagtail]` extra):** audit entries and counters for page publish, unpublish and delete. Add `observe_kit.wagtail_integration` to `INSTALLED_APPS` to register the hooks.
- A startup warning for misordered middleware and system checks for the metrics access settings.

### Security and PII defaults

- Every PII sink (`logs`, `otel`, `sentry`, `audit`) defaults to `BASIC`. What each sink covers differs, and **your own log calls are not sanitized**. See [PII sanitization](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md).
- Above the `NONE` level, the `otel` sink (server span `http.target`) and the `sentry` sink redact credential-style query values such as `token` and `password`, and `audit()` stores such names in `extra`, `before` and `after` as `[Filtered]`. The matching is by [listed name](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#credential-redaction-otel-sentry-and-audit-sinks) only; `audit()` does not include `code`, `key`, `session` or `sig`, or look inside strings. The `logs` sink does not redact credentials.
- A `before_send` you pass to `init_sentry()` runs before scrubbing, so it sees raw data; its result is then scrubbed.
- Sentry metrics and Sentry logs are disabled, because they would bypass scrubbing.
- Incoming `traceparent` / `tracestate` headers are ignored unless `TRUST_INCOMING_TRACE_CONTEXT` or `TRUSTED_TRACE_SOURCES` allows them.
- `X-Tenant-ID` is ignored unless `TENANT_HEADER_TRUSTED` is on and the peer is in `TRUSTED_PROXIES`.
- `X-Forwarded-For` is only read from peers in `TRUSTED_PROXIES` (empty by default), taking the rightmost entry that is not a trusted proxy.
- Prometheus `route` and `tenant` labels are capped at 1000 distinct values each per process by default.
- **`/metrics` is unauthenticated by default.** Set `METRICS_AUTH` to `"token"` or `"staff"` in production.

### Requirements

- Python 3.10 or later; 3.11.5 or later is recommended with the `wagtail` extra (see [known limitations](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#wagtail-extra-on-older-python)).
- Django 5.2 or later and Django REST framework 3.16 or later.
- `sentry-sdk` 2.x only (`>=2.66.1,<3`).

### Known limitations

Scrubbing is a safety net, not a guarantee: among other gaps, Sentry attachments, free-text phone numbers and bare `token=...` text, and several credential parameter names are not redacted. Read [known limitations](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#known-limitations) before relying on it. Notes from before this release are in the [development history](https://github.com/wiphoo/django-observe-kit/blob/main/docs/changelog-pre-0.1.0.md).
