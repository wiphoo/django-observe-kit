# Configuration Reference

All library configuration is loaded by `observe_kit.settings.get_observe_kit_settings()`.

**Resolution order for each key:**

1. `OBSERVE_KIT` dict in Django `settings.py`
2. Environment variable (only for keys marked below)
3. Hardcoded default

If a key is not in `OBSERVE_KIT` and no environment variable is set, the default applies.

## Minimal setup

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "observe_kit",
    "observe_kit.audit",  # optional
]

OBSERVE_KIT = {
    "SERVICE_NAME": "my-app",
    "OTEL_ENDPOINT": "http://localhost:4318",
}
```

When `INSTALLED_APPS` includes `observe_kit`, `ObserveKitConfig.ready()` auto-calls `configure_logging()`, `init_tracing()` (if `SERVICE_NAME` is set), and `init_sentry()` (if `SENTRY_DSN` is set).

## Key reference

### Service identity & exporters

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `SERVICE_NAME` | `str \| None` | `None` | `OTEL_SERVICE_NAME` | Required to enable OTEL tracing. Used as the `service.name` resource attribute. |
| `OTEL_ENDPOINT` | `str \| None` | `None` (SDK default `http://localhost:4318`) | `OTEL_EXPORTER_OTLP_ENDPOINT` | Base OTLP HTTP endpoint. `/v1/traces` and `/v1/logs` are appended automatically. |
| `OTEL_SAMPLE_RATE` | `float \| None` (0.0–1.0) | `None` (≡ 100% sampling, `ALWAYS_ON`) | — | Trace sampling ratio. Values outside `[0,1]` are clamped. Invalid input falls back to `None`. |

### Logging

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `LOG_LEVEL` | `str` | `"INFO"` | `LOG_LEVEL` | Python logging level. Uppercased automatically. |

### Sentry

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `SENTRY_DSN` | `str \| None` | `None` | `SENTRY_DSN` | Required to enable Sentry. |
| `SENTRY_ENVIRONMENT` | `str` | `"production"` | `SENTRY_ENVIRONMENT` | Sentry environment tag. |
| `SENTRY_TRACES_SAMPLE_RATE` | `float` (0.0–1.0) | `0.0` | — | Sentry performance tracing sample rate. Invalid input falls back to `0.0`. |

### PII

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `PII_LEVEL` | `str` | `"BASIC"` | — | Global PII level (`NONE` / `BASIC` / `SENSITIVE`). Applied to every sink unless `PII_LEVELS` is set. Uppercased automatically. |
| `PII_LEVELS` | `dict[str, str] \| None` | `None` | — | Per-sink levels. Keys: `logs`, `otel`, `sentry`, `audit`. If set, overrides `PII_LEVEL` entirely. |
| `PII_HASH_SALT` | `str` | `""` | — | Salt prepended before hashing PII (e.g. IP, user-agent). Set per-environment to prevent rainbow-table reversal. |
| `EXTRA_DROP_HEADERS` | `list[str] \| frozenset[str]` | `frozenset()` | — | Additional header names to drop beyond the built-in set (see [pii.md](pii.md)). Values are lowercased. |
| `EXTRA_MASK_FIELDS` | `list[str] \| frozenset[str]` | `frozenset()` | — | Additional field names to mask. Values are lowercased. |
| `EXTRA_HASH_FIELDS` | `list[str] \| frozenset[str]` | `frozenset()` | — | Additional field names to hash at `SENSITIVE`. Values are lowercased. |

See [`docs/pii.md`](pii.md) for the level/sink action matrix.

### Request handling

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `DB_TRACKING` | `bool` | `True` | — | Enable per-request DB query counting and timing. Disable to remove the connection-wrapping overhead. |
| `TRUSTED_PROXIES` | `list[str]` | `[]` | — | Proxy IPs trusted for `X-Forwarded-For`. Use `["*"]` to trust any. When empty, `REMOTE_ADDR` is used directly. |

### Metrics endpoint access control

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `METRICS_AUTH` | `str` | `"none"` | `OBSERVE_KIT_METRICS_AUTH` | Access mode for the `/metrics` endpoint. One of `"none"` (allow all), `"staff"` (require `request.user.is_staff`), or `"token"` (require `Authorization: Bearer <token>`). Invalid values fall back to `"none"`. When `"none"` and Django `DEBUG` is `False`, a `RuntimeWarning` is emitted once per process. |
| `METRICS_TOKEN` | `str \| None` | `None` | `OBSERVE_KIT_METRICS_TOKEN` | Bearer token required when `METRICS_AUTH == "token"`. Compared with `hmac.compare_digest`. Empty / missing token rejects every request. |

Recommended for production: configure `"token"` mode and pass the token to your Prometheus scrape config under `authorization.credentials`. Use `"staff"` mode when only humans on the Django admin should be able to scrape.

### Master switch

| Key | Type | Default | Env var | Notes |
|---|---|---|---|---|
| `ENABLED` | `bool` | `True` | — | When `False`, `ObserveKitConfig.ready()` is a no-op. Any string other than `"false"` (case-insensitive) evaluates to `True`. |

## Examples

### Production with env vars

```bash
export OTEL_SERVICE_NAME=my-app
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.example.com
export SENTRY_DSN=https://key@sentry.io/123
export SENTRY_ENVIRONMENT=production
export LOG_LEVEL=INFO
```

```python
# settings.py — only library-specific keys without env-var coverage
OBSERVE_KIT = {
    "PII_LEVELS": {
        "logs": "BASIC",
        "otel": "BASIC",
        "sentry": "SENSITIVE",
        "audit": "NONE",
    },
    "PII_HASH_SALT": os.environ["PII_HASH_SALT"],
    "TRUSTED_PROXIES": ["10.0.0.1"],
    "OTEL_SAMPLE_RATE": 0.1,
    "SENTRY_TRACES_SAMPLE_RATE": 0.05,
}
```

### Disable observe_kit in tests

```python
OBSERVE_KIT = {"ENABLED": False}
```

### Manual initialization (no auto-init)

Omit `OBSERVE_KIT` entirely from settings and call the init functions yourself:

```python
from observe_kit.logging import configure_logging
from observe_kit.otel import init_tracing
from observe_kit.sentry import init_sentry

configure_logging(level="INFO")
init_tracing(service_name="my-app", endpoint="http://localhost:4318")
init_sentry(dsn="https://key@sentry.io/123", environment="production")
```

## Related

- [`docs/middleware.md`](middleware.md) — middleware chain and order
- [`docs/pii.md`](pii.md) — PII fields, levels, and per-sink actions
- [`docs/HYPERDX_QUICKSTART.md`](HYPERDX_QUICKSTART.md) — full onboarding example
