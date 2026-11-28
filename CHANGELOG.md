# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.1.0] - 2026-11-28

First release.

### Features

- **Request context and structured logging:** request-scoped context and JSON logs, including a `request_complete` event, with [log record sanitization](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#log-record-sanitization-logs-sink). With `OBSERVE_KIT` set and `observe_kit` in `INSTALLED_APPS`, logging, tracing (when `SERVICE_NAME` is set) and Sentry (when `SENTRY_DSN` is set) are initialised at startup.
- **OpenTelemetry:** traces and log export over OTLP/HTTP, and an `X-Trace-Id` response header.
- **Prometheus:** HTTP request count and duration, database queries and time per request, audit and Wagtail page counters, and a `/metrics` view.
- **Sentry:** `init_sentry()` with PII scrubbing of error and transaction events, and request/trace/tenant context.
- **DRF:** ViewSet action detection, span renaming, and an exception handler.
- **Audit log:** the optional `observe_kit.audit` app stores audit entries with trace and tenant IDs, records logins and logouts, and blocks updates and deletes through the ORM and the admin (not through direct SQL).
- **Wagtail (optional, `[wagtail]` extra):** audit entries and counters for page publish, unpublish and delete. Add `observe_kit.wagtail_integration` to `INSTALLED_APPS` to register the hooks.
- A startup warning for misordered middleware and system checks for the metrics access settings.

### Security and PII defaults

- Every PII sink (`logs`, `otel`, `sentry`, `audit`) defaults to `BASIC`. What each sink covers differs. See [PII sanitization](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md).
- Above the `NONE` level, log records on the JSON output of `configure_logging()` and on the OpenTelemetry log handler of `init_tracing()` are [sanitized](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#log-record-sanitization-logs-sink): field rules and credential redaction for `extra`, dict messages and container arguments, and email masking and URL scrubbing in text. This is a safety net with [many gaps](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#log-records), and other handlers receive the original record.
- Above the `NONE` level, the `otel` sink (server span `http.target`) and the `sentry` sink redact credential-style query values such as `token` and `password`, and `audit()` stores such names in `extra`, `before` and `after` as `[Filtered]`. The matching is by [listed name](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#credential-redaction-otel-sentry-and-audit-sinks) only; `audit()` does not include `code`, `key`, `session` or `sig`, or look inside strings.
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

Scrubbing is a safety net, not a guarantee. Read [known limitations](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#known-limitations) before relying on it.

- **Logs:** free text other than emails and URL credentials is not cleaned (`password=hunter2`, bare tokens, phone numbers, names, positional arguments), nor are unlisted field names. Only the outputs Observe Kit configures are covered; other handlers, including Django's `django` and `django.server` handlers, write the original.
- **Do not log `request.META` or large or sensitive objects.** Under `runserver`, `META` includes the process environment. Every sequence is opened, so logging a paginator `Page` runs its query. `memoryview` and `array.array` values are written as unscrubbed numbers, and other objects as their `str()`.
- **Log size and cost:** large data becomes `[Filtered]`, and a record whose `%` format then fails loses its fields, including `trace_id`. There is no limit on total text size (large crafted input can take many seconds of CPU), and sanitizing costs CPU on every record and output. If reading the settings fails at runtime, every record is replaced by a placeholder.
- **Exporting logs at shutdown:** `handler.flush()`, `logging.shutdown()` and `logging.config.dictConfig()` do not export the queued records of the log handler added by `init_tracing()`. Periodic export continues. A `LoggerProvider` with `shutdown_on_exit=True` (the default) also exports at a normal exit, but processes that end without running `atexit` (a `multiprocessing` fork child leaving through `os._exit`, an unhandled `SIGTERM`) lose queued records. With no collector listening, exit can be delayed by about `OTEL_EXPORTER_OTLP_TIMEOUT` (default 10 seconds).
- **Sentry:** among other gaps, attachments, phone numbers and bare `token=...` in free text, and several credential parameter names are not redacted.
- **Audit:** unlisted names and text inside strings are stored as given, as are `[key, value]` pairs in a set, or in a list that mixes them with other items.

Notes from before this release are in the [development history](https://github.com/wiphoo/django-observe-kit/blob/main/docs/changelog-pre-0.1.0.md).
