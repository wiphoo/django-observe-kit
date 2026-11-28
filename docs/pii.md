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
| `sentry` | `before_send` scrubbing of Sentry events: request headers, `query_string` + `request.url` query, parsed body (`data`), the `user` object, application `extra`, and breadcrumb `data` (honouring `EXTRA_*` fields). `request.cookies` is redacted wholesale. At `SENSITIVE`, the client IP (`request.env.REMOTE_ADDR`, `user.ip_address`) is hashed. | `BASIC` |
| `audit` | `AuditLog` payload sanitization. | `BASIC` |

A custom `before_send` passed to `init_sentry()` does not replace this scrubbing. It runs first and receives the unscrubbed event; whatever it returns is then scrubbed, so don't call `scrub_event()` inside it (hashed fields would be hashed twice). Returning `None` drops the event, as does an exception in the hook or the scrubber.

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

## Credential redaction (`otel` and `sentry` sinks)

At any level above `NONE`, the `otel` and `sentry` sinks redact the values of credential-style **query keys** by default (`observe_kit.conf.QUERY_SECRET_KEYS`, matched case-insensitively): `token`, `password`, `passwd`, `secret`, `api_key`, `apikey`, `access_token`, `refresh_token`, `signature`, `sig`, `code`, `session`, `key`, `x-amz-signature`, `x-amz-credential`, `x-amz-security-token`, `awsaccesskeyid`, `x-goog-signature`. The key is kept and the value becomes `REDACTED`.

- **`otel`** applies this to the server span `http.target` attribute.
- **`sentry`** applies this wherever the event carries a query: `request.url`, `query_string` (string, dict, pair list), `QUERY_STRING` and URI entries in `request.env`, URL-valued headers (`Referer`, ...), spans, the transaction name, breadcrumbs, URLs in messages and exception values, URL-encoded form bodies, and percent-encoded or nested redirect URLs. URL fragments that look like a query (`#access_token=...`, including `http.fragment` / `url.fragment` data) are redacted wholesale.
- **`sentry` request bodies** (`request.data` as a parsed dict or pair list, or a JSON string, nested at any depth) replace the value with `[Filtered]`, but only for the unambiguous subset `BODY_SECRET_KEYS`: the list above **without** `code`, `key`, `session` and `sig`, which are usually ordinary data in a body. A URL-encoded string body is treated as a query and uses the full list.
- **Not covered by this rule:** dict keys in `extra`, `contexts`, `user`, `tags` and other non-request data (URLs stored there are still scrubbed), and bare `token=...` prose that is not part of a URL. Add `EXTRA_MASK_FIELDS` / `EXTRA_DROP_HEADERS` rules for those. The Sentry SDK's own `EventScrubber` separately filters some of these keys.
- **Operator rules win.** If an `EXTRA_DROP_HEADERS`, `EXTRA_MASK_FIELDS` or active `EXTRA_HASH_FIELDS` rule matches the key, that rule is applied instead (note that a mask keeps the first two characters). In `otel`, `EXTRA_DROP_HEADERS` extends the redaction list.
- The `logs` and `audit` sinks do **not** apply this rule.

## Sentry metrics and logs are disabled

`init_sentry()` drops every Sentry metric (`sentry_sdk.metrics.*`) and Sentry log (`sentry_sdk.logger.*`, and the SDK's log capture) before it is sent. Neither is an event, so `before_send` never sees them and their names, bodies and attributes would reach Sentry unscrubbed. This applies at every PII level, including `NONE`, and `init_sentry()` has no option to turn them back on. Errors, transactions and breadcrumbs are unaffected; use the Prometheus metrics in Observe Kit for metrics.

It is done with `before_send_metric` and `before_send_log` because the SDK's own switches are not enough across `sentry-sdk>=2.66.1,<3`: 2.76.0 and 2.77.0 ignore `enable_metrics=False`, and send `sentry_sdk.logger` records even though `enable_logs` defaults to `False` (2.66.1 honours both).

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
