# HyperDX + ClickHouse Quickstart

Get traces **and** logs from your Django app flowing into HyperDX in about 5 minutes.

## What you get

- Every HTTP request produces a **trace** in HyperDX's Trace view (span name, duration, status, DB queries)
- Every Python log call produces a **log record** correlated to that trace by `trace_id`
- Both land in ClickHouse via the OTEL Collector; HyperDX reads them from there

```
Django app
  ├── traces  ──► OTEL Collector :4318 ──► ClickHouse (otel_traces) ──► HyperDX
  └── logs    ──► OTEL Collector :4318 ──► ClickHouse (otel_logs)   ──► HyperDX
```

---

## 1. Start the local stack

```bash
git clone https://github.com/your-org/Django-Observe_Kit
cd Django-Observe_Kit
make integration-up
```

When all services are healthy you'll see:

```
✅ All services are healthy!
  - OTEL Collector HTTP: http://localhost:4318
  - HyperDX:            http://localhost:8080   (admin@example.com / Admin123!@#$)
  - Prometheus:         http://localhost:9090
  - ClickHouse HTTP:    http://localhost:28123
```

---

## 2. Install the library

```bash
pip install observe_kit
```

---

## 3. Configure your Django app

```python
# settings.py

INSTALLED_APPS = [
    # ... your apps ...
    "observe_kit",           # ← add this
    "observe_kit.audit",     # ← optional: audit logging with Django admin support
]

# Auto-init: tracing + log export + structured logging.
# Remove keys you don't need — all are optional.
OBSERVE_KIT = {
    "SERVICE_NAME": "my-django-app",
    "OTEL_ENDPOINT": "http://localhost:4318",   # base URL; /v1/traces and /v1/logs appended automatically
    "LOG_LEVEL": "INFO",
    "PII_LEVEL": "BASIC",                        # NONE / BASIC / SENSITIVE
    # "SENTRY_DSN": "https://key@sentry.io/123",  # uncomment to enable Sentry
}

# Add middleware (order is important).
MIDDLEWARE = [
    "observe_kit.otel.middleware.TraceContextMiddleware",       # 1. extract/create trace
    "observe_kit.context_middleware.RequestContextMiddleware",  # 2. init per-request context
    "observe_kit.context_middleware.UserLoggingContextMiddleware",  # 3. attach user_id
    # "observe_kit.drf.integration.DRFIntegrationMiddleware",   # optional: DRF ViewSet span names
    "observe_kit.logging.middleware.RequestLoggingMiddleware",  # 4. log request_complete
    "observe_kit.metrics.middleware.PrometheusRequestMiddleware",  # 5. Prometheus metrics
    "observe_kit.sentry.middleware.SentryContextMiddleware",    # 6. Sentry enrichment
    # ... rest of your Django middleware ...
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# Expose /metrics for Prometheus (optional).
# urls.py:  path("", include("observe_kit.urls"))
```

`INSTALLED_APPS` registration triggers `ObserveKitConfig.ready()` which calls
`configure_logging()`, `init_tracing()`, and optionally `init_sentry()` automatically —
no manual `init_*()` calls needed.

If you prefer manual control, omit `OBSERVE_KIT` and call the functions yourself:

```python
from observe_kit.logging import configure_logging
from observe_kit.otel import init_tracing

configure_logging(level="INFO")
init_tracing(service_name="my-app", endpoint="http://localhost:4318")
```

---

## 4. Run your app and make requests

```bash
python manage.py runserver
curl http://localhost:8000/api/users/
```

---

## 5. Open HyperDX

Navigate to **http://localhost:8080** and log in with `admin@example.com` / `Admin123!@#$`.

- **Traces** tab → your requests appear as spans with `service.name = my-django-app`
- **Logs** tab → `request_complete` events with `trace_id` matching the spans
- Click a trace → switch to its correlated logs instantly

---

## Production

Set environment variables instead of hardcoding in `settings.py`:

```bash
OTEL_SERVICE_NAME=my-app
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-otel-collector.example.com
SENTRY_DSN=https://key@sentry.io/123
SENTRY_ENVIRONMENT=production
```

`OBSERVE_KIT` dict keys take precedence over env vars, so you can mix both.

---

## Per-sink PII control

```python
OBSERVE_KIT = {
    "SERVICE_NAME": "my-app",
    "PII_LEVELS": {
        "logs":   "BASIC",      # drop auth headers, mask email/phone
        "otel":   "BASIC",
        "sentry": "SENSITIVE",  # also hash user-agent and IP
        "audit":  "NONE",       # no sanitisation in audit trail
    },
}
```

---

## Troubleshooting

**No data in HyperDX?**
1. Check the OTEL Collector is receiving: `curl http://localhost:13133` (health endpoint)
2. Check ClickHouse tables exist: `curl http://localhost:28123/?query=SHOW+TABLES+IN+default`
3. Check `init_tracing()` was called (either via `OBSERVE_KIT.SERVICE_NAME` or manually)
4. Wait ~10 seconds — the collector batches spans before flushing to ClickHouse

**Traces appear but logs don't?**
- Ensure `init_tracing()` is called (log export is wired inside it)
- The OTEL Collector's `logs` pipeline must be present in `otel-collector.yaml` (it is by default in this repo)

**Port conflicts?**
```bash
make integration-check-ports
```
Override with env vars: `OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT=14318 make integration-up`
