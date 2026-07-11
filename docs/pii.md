# PII Sanitization Reference

Observe Kit applies PII rules to headers, query params, and parsed request bodies before they reach logs, OpenTelemetry spans, Sentry events, or the audit log. Rules are defined in `src/observe_kit/pii_rules.py` and `src/observe_kit/conf.py`.

## Concepts

- **Level** — `NONE`, `BASIC`, or `SENSITIVE`. Controls *how aggressive* sanitization is.
- **Sink** — `logs`, `otel`, `sentry`, `audit`. Controls *where* a given level applies.
- **Field sets** — three categories: `DROP_HEADERS`, `MASK_FIELDS`, `HASH_FIELDS`. Each level activates a different combination.

You configure level per sink via `OBSERVE_KIT["PII_LEVELS"]`. You extend field sets via `OBSERVE_KIT["EXTRA_DROP_HEADERS"]`, `EXTRA_MASK_FIELDS`, `EXTRA_HASH_FIELDS`.

## Levels

| Level | What it does |
|---|---|
| `NONE` | No sanitization. Values pass through unchanged. |
| `BASIC` | Drops headers in `DROP_HEADERS`. Masks values whose key is in `MASK_FIELDS`. |
| `SENSITIVE` | All of `BASIC`, plus hashes values whose key is in `HASH_FIELDS`. |

Levels are case-insensitive in `OBSERVE_KIT` (e.g. `"basic"` works), but stored uppercase internally.

## Sinks and defaults

| Sink | Used by | Default level |
|---|---|---|
| `logs` | `RequestContextMiddleware` (header & query-param sanitization), `RequestLoggingMiddleware`, `audit/` body sanitization for log events. | `BASIC` |
| `otel` | OTEL span attribute enrichment. | `BASIC` |
| `sentry` | Sentry scope tags and breadcrumbs. | `BASIC` |
| `audit` | `AuditLog` payload sanitization. | `BASIC` |

Defaults are defined in `observe_kit.conf.DEFAULT_PII_LEVELS`.

If `OBSERVE_KIT["PII_LEVELS"]` is set, it overrides per sink. If only `OBSERVE_KIT["PII_LEVEL"]` is set, it applies to every sink. Setting both means `PII_LEVELS` wins (see `ObserveKitSettings.effective_pii_levels`).

## Built-in field sets

From `observe_kit/conf.py`:

| Set | Members | Action at applicable level |
|---|---|---|
| `DROP_HEADERS` | `authorization`, `cookie`, `set-cookie`, `x-api-key`, `x-access-token` | Removed from the mapping at `BASIC` and `SENSITIVE`. |
| `MASK_FIELDS` | `email`, `phone` | Replaced with a masked value at `BASIC` and `SENSITIVE`. |
| `HASH_FIELDS` | `user-agent`, `ip` | Replaced with `sha256(salt + value)` at `SENSITIVE` only. |

Keys are matched **case-insensitively**.

## Action matrix

For a key/value pair where the key falls into a built-in set:

| Key category | Level `NONE` | Level `BASIC` | Level `SENSITIVE` |
|---|---|---|---|
| In `DROP_HEADERS` | pass through | **dropped** | **dropped** |
| In `MASK_FIELDS` | pass through | **masked** | **masked** |
| In `HASH_FIELDS` | pass through | pass through | **hashed (salted SHA-256)** |
| Other key | pass through | pass through | pass through |

## Masking & hashing details

- **Masking** (`_mask_value`, `pii_rules.py:89-95`):
  - If value contains `@`: keeps the first character of the local part, then `***@<domain>`. Example: `alice@example.com` → `a***@example.com`.
  - Otherwise: first two characters + `***`. Example: `5551234567` → `55***`. Short values (≤2 chars) become `***`.
- **Hashing** (`_hash_value`, `pii_rules.py:98-99`): `sha256((PII_HASH_SALT + value).utf-8).hexdigest()`. Set `OBSERVE_KIT["PII_HASH_SALT"]` per-environment to a non-empty secret to make hashes unstable across environments and resist rainbow tables.

## Body sanitization

`sanitize_body` (`pii_rules.py:167-205`) recursively walks parsed JSON-like structures:

- `NONE` returns the body unchanged.
- `BASIC` / `SENSITIVE` drop keys in `DROP_HEADERS`, mask keys in `MASK_FIELDS`, and (at `SENSITIVE`) hash keys in `HASH_FIELDS`.
- For `MASK_FIELDS` keys whose value is not a string, the value is replaced with the literal string `"***"`.
- Lists are walked element-by-element; scalars only get sanitized when their *parent key* matches a sanitized field.

Raw request/response bodies are **never logged**. See `observe_kit.conf.BODY_LOG_WARNING`.

## Extending the field sets

```python
OBSERVE_KIT = {
    "EXTRA_DROP_HEADERS": ["x-internal-token", "x-csrf-token"],
    "EXTRA_MASK_FIELDS": ["ssn", "credit_card"],
    "EXTRA_HASH_FIELDS": ["session_id"],
}
```

Values are lowercased automatically. They merge with the built-in sets; you cannot remove built-in entries via configuration.

## Configuring per sink

```python
OBSERVE_KIT = {
    "SERVICE_NAME": "my-app",
    "PII_LEVELS": {
        "logs":   "BASIC",      # keep operational debuggability
        "otel":   "BASIC",      # spans stay readable
        "sentry": "SENSITIVE",  # external service: hash user-agent and IP
        "audit":  "NONE",       # internal audit trail keeps raw values
    },
    "PII_HASH_SALT": os.environ["PII_HASH_SALT"],
}
```

## Worked example

Request:

```
GET /api/users?email=alice@example.com&page=2
Headers:
  Authorization: Bearer abc123
  X-Forwarded-For: 203.0.113.5
  User-Agent: curl/8.0
```

With `PII_LEVELS["logs"] = "BASIC"` and salt `"s3cret"`:

- `Authorization` header → dropped
- `User-Agent` header → pass through (not in `DROP_HEADERS`, hashing only applies at `SENSITIVE`)
- Query param `email=alice@example.com` → `a***@example.com`
- Query param `page=2` → unchanged

With `PII_LEVELS["sentry"] = "SENSITIVE"` and salt `"s3cret"`:

- `Authorization` header → dropped
- `User-Agent` header → `sha256("s3cret" + "curl/8.0")`
- Query param `email=alice@example.com` → `a***@example.com`
- Query param `page=2` → unchanged

The same request can land in `logs` and `sentry` at the same time with different fidelity.

## Related

- [`docs/configuration.md`](configuration.md) — all `OBSERVE_KIT` keys, including `PII_LEVEL`, `PII_LEVELS`, `PII_HASH_SALT`, and the `EXTRA_*` overrides
- [`docs/middleware.md`](middleware.md) — where sanitization runs in the request lifecycle
- `src/observe_kit/pii_rules.py`, `src/observe_kit/conf.py` — implementation
