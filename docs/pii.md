# PII Sanitization Reference

Observe Kit applies PII rules to data it handles for OpenTelemetry spans, Sentry events, the audit log, and the logging request context. It does not sanitize your own log calls, and it cannot recognise every secret: read [Known limitations](#known-limitations) before relying on it. Rules are defined in `src/observe_kit/pii_rules.py` and `src/observe_kit/conf.py`.

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
| `logs` | Headers and query params that `RequestContextMiddleware` stores in the request context. Log records themselves are [not sanitized](#known-limitations). | `BASIC` |
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

> **Do not put credentials such as `password` or `token` in `EXTRA_HASH_FIELDS`.** The hash is one fast, salted SHA-256, meant for pseudonymisation, not for protecting secrets: anyone who has the salt can test guesses against it, and with an empty `PII_HASH_SALT` (the default) anyone who sees the hash can. At `SENSITIVE`, such a rule also replaces the [default credential redaction](#credential-redaction-otel-and-sentry-sinks) with the hash; at `BASIC` it does not hash anything. To remove a credential, use `EXTRA_DROP_HEADERS`.

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

Observe Kit itself **never logs** raw request/response bodies. See `observe_kit.conf.BODY_LOG_WARNING`.

## Extending the field sets

```python
OBSERVE_KIT = {
    "EXTRA_DROP_HEADERS": ["x-internal-token", "x-csrf-token"],
    "EXTRA_MASK_FIELDS": ["ssn", "credit_card"],
    "EXTRA_HASH_FIELDS": ["customer_id"],
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

In the request context, with `PII_LEVELS["logs"] = "BASIC"` and salt `"s3cret"`:

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

## Known limitations

Scrubbing works on field names and recognisable patterns. Treat it as a safety net, not a guarantee, and keep secrets out of what you send. The following are **not** protected, checked with default settings in 0.1.0.

### Sentry

- **Attachments** (for example `scope.add_attachment(...)`) are sent as given.
- **Bare credentials in free text.** `token=...` or `password=...` in a message is not redacted, and neither is a `?token=...` with no URL or path before it. A URL or path such as `/cb?token=...` is redacted.
- **Phone numbers in free text** are not redacted, including `phone=0812345678` written in prose. Email addresses in free text are masked.
- **Credential names not on the list.** Names are matched exactly against the [list above](#credential-redaction-otel-and-sentry-sinks), so `client_secret`, `id_token`, and bracketed names such as `token[]` or `user[password]` keep their values in URLs, query strings and request bodies. (A nested body such as `{"user": {"password": "..."}}` is redacted.) Adding the exact name to `EXTRA_DROP_HEADERS` removes it from URL queries and parsed request bodies; it does not help in free text.
- **`;`-separated query pairs** (`?a=1;token=...`) are not split, so the later value is not redacted.

### `logs` sink

- **Your log records are not sanitized at any PII level.** The message and `extra={...}` fields you pass to a logger are written out as given, including `email`, `phone`, `password` and tokens. OpenTelemetry log export does not sanitize them either.
- The `logs` level only applies to the headers and query params kept in the request context. These get the built-in and `EXTRA_*` field rules but **no credential redaction**: for example, a `token` query parameter or an `X-Auth-Token` header is kept raw.

### `audit` sink

- **No credential redaction.** With default settings, `audit()` stores a `password`, `token` or `api_key` passed in `extra`, `before` or `after` in the database exactly as given (`email` and `phone` are masked). Remove secrets before calling it, or add their exact names to `EXTRA_DROP_HEADERS`.

### Wagtail extra on older Python

As of 2026-11-28, installing `django-observe-kit[wagtail]` resolves releases with published security advisories on some interpreters, because the patched releases do not support them: Python 3.10 gets a vulnerable `pillow` and `soupsieve`, and Python 3.11 before 3.11.5 gets a vulnerable `soupsieve`. Use **Python 3.11.5 or later** with the `wagtail` extra.

## Related

- [`docs/configuration.md`](configuration.md) — all `OBSERVE_KIT` keys, including `PII_LEVEL`, `PII_LEVELS`, `PII_HASH_SALT`, and the `EXTRA_*` overrides
- [`docs/middleware.md`](middleware.md) — where sanitization runs in the request lifecycle
- `src/observe_kit/pii_rules.py`, `src/observe_kit/conf.py` — implementation
