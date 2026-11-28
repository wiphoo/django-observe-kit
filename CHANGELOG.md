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
- **Audit log:** the optional `observe_kit.audit` app stores audit entries with trace and tenant IDs, records logins and logouts, and refuses updates and deletes through ordinary ORM calls (`save()` and `delete()` on a row, `update()` and `delete()` through `AuditLog.objects`) and the admin. This is [not a guarantee](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#audit-sink): for example `AuditLog._base_manager`, `bulk_create(update_conflicts=True)`, deleting the actor's user (`actor` is cleared) and direct SQL bypass it.
- **Wagtail (optional, `[wagtail]` extra):** audit entries and counters for page publish, unpublish and delete. Add `observe_kit.wagtail_integration` to `INSTALLED_APPS` to register the hooks.
- **Tenant ID:** `request.tenant.id`, else a trusted `X-Tenant-ID`, else the first label of a host name with three or more labels (not `www` or `localhost`, never from an IP-address host). The client controls the host within `ALLOWED_HOSTS`, so do not use it for authorization. A host-derived value keeps its case and has no length check; `AuditLog.tenant_id` is declared with a maximum of 128.
- A startup warning for misordered middleware and system checks for the metrics access settings.

### Security and PII defaults

- Every PII sink (`logs`, `otel`, `sentry`, `audit`) defaults to `BASIC`. What each sink covers differs. See [PII sanitization](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md).
- Above the `NONE` level, log records on the JSON output of `configure_logging()` and on the OpenTelemetry log handler of `init_tracing()` are [sanitized](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#log-record-sanitization-logs-sink): field rules and credential redaction for `extra`, dict messages and container arguments, and email masking and URL scrubbing in text. This is a safety net with [many gaps](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#log-records), and other handlers receive the original record.
- Above the `NONE` level, the `otel` and `sentry` sinks redact credential-style query values such as `token` and `password` in URLs, and `audit()` stores such names in `extra`, `before` and `after` as `[Filtered]`. The matching is by [listed name](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#credential-redaction-otel-sentry-and-audit-sinks) only; `audit()` does not include `code`, `key`, `session` or `sig`, or look inside strings.
- Above the `NONE` level, what Observe Kit writes to its [spans](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#span-sanitization-otel-sink) (names, string attributes, exception events and status descriptions) has emails masked and URL credentials redacted. This has [gaps](https://github.com/wiphoo/django-observe-kit/blob/main/docs/pii.md#tracing), and your own spans, attributes and events are not sanitized.
- Sentry events do not include stack-frame local variables unless you call `init_sentry(..., include_local_variables=True)`. The `OBSERVE_KIT["SENTRY_DSN"]` auto-initialisation has no setting for this. With it on, scrubbing misses values such as names, `client_secret`, and `password` / `token` keys nested in a local. Source lines around each frame are still sent.
- A `before_send` you pass to `init_sentry()` runs before scrubbing, so it sees raw data; its result is then scrubbed.
- Sentry metrics and Sentry logs are disabled, because they would bypass scrubbing.
- Incoming `traceparent` / `tracestate` headers are ignored unless `TRUST_INCOMING_TRACE_CONTEXT` is on. `TRUSTED_TRACE_SOURCES` only narrows that trust to listed client IPs; on its own it does nothing.
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
- **Tracing:** among other gaps, tokens in path segments, `token=...` or `?token=...` in free text with no URL or path before it, secrets written as literals in source lines shown in stack traces, and everything your own code adds (including exceptions the OpenTelemetry SDK records on your child spans) are exported as is. `logo@2x.png` is masked like an email. Request spans are named after the raw path (high cardinality), except for DRF ViewSets with `DRFIntegrationMiddleware`.
- **Do not use log level `DEBUG` with OpenTelemetry log export in production:** with a reachable collector, exporting creates `urllib3` records that are exported in turn, continuously.
- **Exporting logs at shutdown:** `handler.flush()`, `logging.shutdown()` and `logging.config.dictConfig()` do not export the queued records of the log handler added by `init_tracing()`. Periodic export continues. A `LoggerProvider` with `shutdown_on_exit=True` (the default) also exports at a normal exit, but processes that end without running `atexit` (a `multiprocessing` fork child leaving through `os._exit`, an unhandled `SIGTERM`) lose queued records. With an unreachable collector, exit can take about `OTEL_EXPORTER_OTLP_TIMEOUT` (default 10 seconds) or longer, which may exceed a 10-second container stop timeout.
- **Sentry:** among other gaps, attachments, phone numbers and bare `token=...` in free text, and credential names such as `client_secret` and `csrfmiddlewaretoken` are not redacted. Source lines are sent, and cron check-ins are still sent. Breadcrumbs from logging come from the original record, so **clean console logs do not mean clean breadcrumbs** (`request.META` headers and cookies, nested `extra` values, `QueryDict` values and header dumps can reach Sentry). Calling `sentry_sdk.init()` yourself afterwards replaces the scrubbing client.
- **Audit:** unlisted names (such as `csrfmiddlewaretoken`) and text inside strings (including URL queries in header values such as `Referer`) are stored as given, as are `[key, value]` pairs in a set, or in a list that mixes them with other items.

Notes from before this release are in the [development history](https://github.com/wiphoo/django-observe-kit/blob/main/docs/changelog-pre-0.1.0.md).
