# PII Sanitization Reference

Observe Kit applies PII rules to data it handles for OpenTelemetry spans, Sentry events, the audit log, the logging request context, and the log records written by the [outputs it configures](#log-record-sanitization-logs-sink). It cannot recognise every secret: read [Known limitations](#known-limitations) before relying on it. Rules are defined in `src/observe_kit/pii_rules.py` and `src/observe_kit/conf.py`.

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
| `logs` | [Log records](#log-record-sanitization-logs-sink) on the outputs Observe Kit configures, and the headers and query params that `RequestContextMiddleware` stores in the request context. | `BASIC` |
| `otel` | The [spans Observe Kit writes](#span-sanitization-otel-sink): names, string attributes, exception events and status descriptions. | `BASIC` |
| `sentry` | `before_send` scrubbing of Sentry events: request headers, `query_string` + `request.url` query, parsed body (`data`), the `user` object, application `extra`, and breadcrumb `data` (honouring `EXTRA_*` fields). `request.cookies` is redacted wholesale. At `SENSITIVE`, the client IP (`request.env.REMOTE_ADDR`, `user.ip_address`) is hashed. | `BASIC` |
| `audit` | `AuditLog` payload sanitization, including [credential redaction](#credential-redaction-otel-sentry-and-audit-sinks). | `BASIC` |

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

- **Masking** (`_mask_value` in `pii_rules.py`):
  - If value contains `@`: keeps the first character of the local part, then `***@<domain>`. Example: `alice@example.com` → `a***@example.com`.
  - Otherwise: first two characters + `***`. Example: `5551234567` → `55***`. Short values (≤2 chars) become `***`.
- **Hashing** (`_hash_value` in `pii_rules.py`): `sha256((PII_HASH_SALT + value).utf-8).hexdigest()`. Set `OBSERVE_KIT["PII_HASH_SALT"]` per-environment to a non-empty secret to make hashes unstable across environments and resist rainbow tables.

> **Do not put credentials such as `password`, `token` or `client_secret` in `EXTRA_HASH_FIELDS`.** The hash is one fast, salted SHA-256, meant for pseudonymisation, not for protecting secrets: anyone who has the salt can test guesses against it, and with an empty `PII_HASH_SALT` (the default) anyone who sees the hash can. What such a rule does depends on the level and on whether the name is on the [credential redaction list](#credential-redaction-otel-sentry-and-audit-sinks):
>
> - **At `SENSITIVE`**, a listed name is hashed instead of redacted in the `sentry` and `audit` sinks, in [log records](#log-record-sanitization-logs-sink) and in URL queries in `otel` span text such as exception messages (the `otel` attribute `http.target` keeps `REDACTED`). A name that is not listed is stored or sent as the guessable hash.
> - **At `BASIC`**, nothing is hashed. Listed names are still redacted where redaction applies, but the rule gives a name that is not listed no protection: it stays raw.
>
> To remove a credential, use `EXTRA_DROP_HEADERS`.

## Credential redaction (`otel`, `sentry` and `audit` sinks)

At any level above `NONE`, the `otel` and `sentry` sinks redact the values of credential-style **query keys** by default (`observe_kit.conf.QUERY_SECRET_KEYS`, matched case-insensitively): `token`, `password`, `passwd`, `secret`, `api_key`, `apikey`, `access_token`, `refresh_token`, `signature`, `sig`, `code`, `session`, `key`, `x-amz-signature`, `x-amz-credential`, `x-amz-security-token`, `awsaccesskeyid`, `x-goog-signature`. The key is kept and the value becomes `REDACTED`.

- **`otel`** applies this to the server span `http.target` attribute, and to URLs and paths with a query in the other [span text](#span-sanitization-otel-sink) Observe Kit writes.
- **`sentry`** applies this wherever the event carries a query: `request.url`, `query_string` (string, dict, pair list), `QUERY_STRING` and URI entries in `request.env`, URL-valued headers (`Referer`, ...), spans, the transaction name, breadcrumbs, URLs in messages and exception values, URL-encoded form bodies, and percent-encoded or nested redirect URLs. URL fragments that look like a query (`#access_token=...`, including `http.fragment` / `url.fragment` data) are redacted wholesale.
- **`sentry` request bodies** (`request.data` as a parsed dict or pair list, or a JSON string, nested at any depth) replace the value with `[Filtered]`, but only for the unambiguous subset `BODY_SECRET_KEYS`: the list above **without** `code`, `key`, `session` and `sig`, which are usually ordinary data in a body. A URL-encoded string body is treated as a query and uses the full list.
- **`audit`** applies the same `BODY_SECRET_KEYS` subset to the `extra`, `before` and `after` payloads of `audit()`. A value under a listed name is stored as `[Filtered]`, whatever its type (a whole nested structure included), at any depth in dicts, lists, tuples and `[key, value]` pair lists. Names are matched exactly but case-insensitively; empty strings and `None` are kept. It does not look inside string values.
- **Not covered in Sentry events:** dict keys in `extra`, `contexts`, `user`, `tags` and other non-request data (URLs stored there are still scrubbed), and bare `token=...` prose that is not part of a URL. Add `EXTRA_MASK_FIELDS` / `EXTRA_DROP_HEADERS` rules for those. The Sentry SDK's own `EventScrubber` separately filters some of these keys.
- **Operator rules win in `sentry`, `audit` and most `otel` span text.** If an `EXTRA_DROP_HEADERS`, `EXTRA_MASK_FIELDS` or active (`SENSITIVE` only) `EXTRA_HASH_FIELDS` rule matches the key, that rule is applied instead. Both can be weaker than the redaction: a mask keeps the first two characters, and a hash can be guessed. In the `otel` attribute `http.target`, names on the list stay `REDACTED` whatever the mask or hash rules say, and `EXTRA_DROP_HEADERS` extends the list. That does **not** hold for URL queries elsewhere in span text (such as exception messages and stack traces): there an `EXTRA_MASK_FIELDS` rule, or at `SENSITIVE` an `EXTRA_HASH_FIELDS` rule, for a listed name replaces `REDACTED`.
- At `NONE` Observe Kit redacts nothing, and `EXTRA_*` rules do not apply either.
- [Log records](#log-record-sanitization-logs-sink) use both lists in their own way. The headers and query params kept in the request context get **no credential redaction**.

## Span sanitization (`otel` sink)

At `BASIC` (the default) and `SENSITIVE`, Observe Kit cleans what **it** writes to spans: span names (including DRF renames), the string attributes it sets (such as `url.path`, `http.target`, `http.path`, `http.route`, `server.address`, `tenant.id` and `enduser.id`), exception events (type, message and the rendered stack trace) and status descriptions, on its request spans and Wagtail hook spans.

- Emails (also percent-encoded) are masked, credential query values in URLs and paths (the [credential list](#credential-redaction-otel-sentry-and-audit-sinks)) become `REDACTED`, and `user:password@` in a URL is replaced by `[Filtered]@`. `http.target` query parameters also get the field rules.
- A value that cannot be sanitized is exported as `[Filtered]`.
- Only the exported copy changes: your code still receives the original exception.
- `PII_LEVELS["otel"] = "NONE"` turns it off.

This is a safety net: read the [tracing limitations](#tracing).

## Log record sanitization (`logs` sink)

At `BASIC` (the default) and `SENSITIVE`, Observe Kit sanitizes each log record on the two outputs it configures:

- the JSON formatter `observe_kit.logging.config.RequestFormatter`, used by the console handler of `configure_logging()`;
- the OpenTelemetry log handler added by `init_tracing()`.

Each output works on a copy. The record, your `extra` objects and your arguments are not modified, and other handlers receive the original. What happens on these two outputs:

- **`extra` fields and dict messages** get the field rules (built-in and `EXTRA_*`): for example `authorization` and `cookie` are removed, `email` and `phone` masked, and `ip` and `user-agent` hashed at `SENSITIVE`. Values under a `BODY_SECRET_KEYS` name (the [credential list](#credential-redaction-otel-sentry-and-audit-sinks) without `code`, `key`, `session` and `sig`) become `[Filtered]`, unless an active operator rule for that name applies instead. An inactive `EXTRA_HASH_FIELDS` rule (at `BASIC`) does not turn redaction off.
- **Containers are opened** so that the rules see inside: mappings, sequences, sets, dict views, named tuples and dataclasses (every field except `field(repr=False)` ones). The same rules apply to containers passed as `%` arguments. In `request.META`-style keys, `HTTP_*` also matches its header name, `*_COOKIE` and `cookies` keys are removed, and `QUERY_STRING` is scrubbed as a query. Other objects are written as their `str()`, on both outputs.
- **Free text** (the formatted message, `extra` strings and keys, exception text and stack info) only has emails masked and URLs scrubbed: credential query values (the full list, including `code`, `key`, `session` and `sig`) become `REDACTED`, and `user:password@` is removed.
- **Format changes:** the message is formatted before sanitizing, so the sanitized record has no `args`. In OpenTelemetry export, `exc_info` is replaced by sanitized `exception.type`, `exception.message` and `exception.stacktrace` attributes. `bytes` are written as text, and a rewritten URL query is re-encoded.
- **Failures:** a record that cannot be sanitized (for example a bad format string) is replaced by `[Filtered] observe_kit: log record could not be sanitized` with only the logger, level and source location. Its `extra` and request fields, such as `trace_id`, are not written.

`PII_LEVELS["logs"] = "NONE"` turns this off. Read the [limits](#log-records) before relying on it.

## Sentry metrics and logs are disabled

`init_sentry()` drops every Sentry metric (`sentry_sdk.metrics.*`) and Sentry log (`sentry_sdk.logger.*`, and the SDK's log capture) before it is sent. Neither is an event, so `before_send` never sees them and their names, bodies and attributes would reach Sentry unscrubbed. This applies at every PII level, including `NONE`, and `init_sentry()` has no option to turn them back on. Errors, transactions and breadcrumbs are unaffected; use the Prometheus metrics in Observe Kit for metrics.

It is done with `before_send_metric` and `before_send_log` because the SDK's own switches are not enough across `sentry-sdk>=2.66.1,<3`: 2.76.0 and 2.77.0 ignore `enable_metrics=False`, and send `sentry_sdk.logger` records even though `enable_logs` defaults to `False` (2.66.1 honours both).

## Body sanitization

`sanitize_body` (in `pii_rules.py`) recursively walks parsed JSON-like structures:

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
        "audit":  "NONE",       # keeps raw values, including passwords and tokens
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

### Tracing

- **Tokens in path segments**, such as `/reset/<token>/`, are exported as is.
- **`token=...` or `?token=...` in free text** that is not attached to a URL or path (for example in an exception message) is not redacted. Neither are other free-text secrets and personal data (names, phone numbers, bare tokens) or unlisted query names such as `client_secret`.
- **Source lines in stack traces.** `exception.stacktrace` includes lines of source code, so a secret written there as a literal is exported unless it matches a pattern that is redacted.
- **Your own telemetry** is not sanitized: spans, attributes and events your code creates, including those you add to the current request span. When an exception leaves one of your own child spans, the OpenTelemetry SDK records it there **unsanitized** by default. Observe Kit does not sanitize spans from other instrumentation either.
- **Over-masking:** `logo@2x.png` is masked like an email, and a percent-encoded value that also has an `@` (such as the path `/@user/a%20b`) can become `[Filtered]`.
- **Span names and routes:** a request span is named after the **raw path** (`GET /users/42/`), even when `http.route` holds the route pattern. Only DRF ViewSets with `DRFIntegrationMiddleware` are renamed (`drf.<ViewSet>.<action>`). For an unmatched URL, `http.route` is the raw path too. Raw paths have high cardinality.
- **`NONE`** leaves spans unsanitized.

### Sentry

- **Source code lines** around each stack frame are sent, so a secret written as a literal in source can reach Sentry.
- **Stack-frame local variables** are not sent by default (`init_sentry(include_local_variables=False)`; the `OBSERVE_KIT["SENTRY_DSN"]` auto-initialisation has no setting to turn them on). To send them, call `init_sentry(..., include_local_variables=True)` yourself, and leave the DSN out of `OBSERVE_KIT` and the `SENTRY_DSN` environment variable. The usual scrubbing then applies but **misses a lot**: for example names, a phone number in a variable not named `phone`, `client_secret`, and `password` / `token` keys nested inside a local are sent as is.
- **Breadcrumbs from logging are not log-sanitized.** The SDK's logging integration captures the original record, so [log record sanitization](#log-record-sanitization-logs-sink) does not apply to breadcrumbs, which get the Sentry scrubbing only: **clean console logs do not mean clean breadcrumbs**. In tests where the console output did not contain the value, Sentry still received `Authorization` and cookie values (from `request.META` header entries passed in `extra`, and from request headers passed as a log argument), a nested `password` in an `extra` dict, and a password in a `QueryDict` passed in `extra`.
- **`csrfmiddlewaretoken`** is not on the credential list, so a submitted form's CSRF token is sent in the request body. The log and audit sinks keep it too.
- **Cron check-ins** (`sentry_sdk.monitor`, `capture_checkin`) are still sent. They pass through your `before_send` hook (returning `None` drops them) and then the event scrubbing, which has the gaps listed here.
- **Your own `sentry_sdk.init()` call** made after `init_sentry()`, with or without `data_collection`, replaces its client: neither Observe Kit's scrubbing nor the metrics and logs drop applies, and the SDK's defaults apply, which send local variables.

- **Attachments** (for example `scope.add_attachment(...)`) are sent as given.
- **Bare credentials in free text.** `token=...` or `password=...` in a message is not redacted, and neither is a `?token=...` with no URL or path before it. A URL or path such as `/cb?token=...` is redacted.
- **Phone numbers in free text** are not redacted, including `phone=0812345678` written in prose. Email addresses in free text are masked.
- **Credential names not on the list.** Names are matched exactly against the [list above](#credential-redaction-otel-sentry-and-audit-sinks), so `client_secret`, `id_token`, and bracketed names such as `token[]` or `user[password]` keep their values in URLs, query strings and request bodies. (A nested body such as `{"user": {"password": "..."}}` is redacted.) Adding the exact name to `EXTRA_DROP_HEADERS` removes it from URL queries and parsed request bodies; it does not help in free text.
- **`;`-separated query pairs** (`?a=1;token=...`) are not split, so the later value is not redacted.

### Log records

Log sanitization is the narrowest safety net here. **Do not log secrets, personal data, `request.META`, or large objects.**

**Not cleaned**

- **Free text**, apart from emails and URLs: `password=hunter2` or `client_secret: ...` in a sentence, bare tokens, phone numbers, names, and `?token=...` with no URL or path before it. This covers positional arguments (`logger.info("password %s", value)`), the `str()` of any object, and a dict inside an exception message.
- **Names that are not listed:** `client_secret`, `csrfmiddlewaretoken`, `id_token`, `user[password]` or an `x-auth-token` key keep their values, as do `code`, `key`, `session` and `sig` outside URL queries and `QUERY_STRING`. Names and addresses under any other key are not recognised.
- **Mixed collections:** a list or set that mixes `[key, value]` pairs with other items is not read as pairs.
- **A cookie dict** under another name, or passed as the message or an argument (`request.COOKIES`), keeps `sessionid`.
- **The rest of `request.META`.** Only header, cookie and `QUERY_STRING` entries are treated. Under `runserver`, `META` includes the process environment, so its secrets are written unless their names are listed.
- **Dataclasses with a hand-written `__repr__`:** hiding a field there does not hide it from the logs.
- **The headers and query params kept in the request context** get the built-in and `EXTRA_*` field rules but no credential redaction.

**Outputs not covered**

- Handlers and formatters Observe Kit does not configure: your own (including those you pass through `configure_logging(extra=...)` without `RequestFormatter`), Django's `django` and `django.server` handlers, and `logging.lastResort`. Sentry has its own rules, above.
- If writing to the stream fails, Python's `logging` error handler prints the original, unsanitized message and arguments.
- A custom `RequestFormatter` format string that uses `%(exc_info)s` or other core record fields has not been tested. An attribute named like a core `LogRecord` field is treated as one.

**Side effects, size and cost**

- **Every sequence is opened.** A Django paginator `Page` is a sequence: logging one runs its query. In `extra`, every row of that page is then written. `memoryview` and `array.array` values are written as lists of numbers, so text inside them is not scrubbed.
- **Large data is cut off.** At most 20,000 entries and items are copied per record and output, and nesting stops at 50 levels. What is cut off becomes `[Filtered]`: a list is replaced whole, and containers processed after the budget is spent become `[Filtered]` too. If a `%` format string no longer fits a cut-off argument (for example a large dict used with `%(name)s`), the whole record becomes the failure placeholder and loses its request fields, including `trace_id`.
- **There is no limit on total text size.** The 20,000 budget counts items, not characters. In a test on one developer machine, a single record with 1,000 crafted 10 KB strings took about 20 seconds of CPU on one output. Do not log unbounded or user-controlled bulk data.
- **Every record costs more CPU:** in microbenchmarks, roughly 5 to 11 times the console CPU time of the unsanitized formatter (about 0.1 to 0.2 ms for a small record), linear in record size and paid again for each output.
- **An invalid setting blanks the logs.** If reading `OBSERVE_KIT` settings fails at runtime, even on an unrelated key such as `METRICS_AUTH`, every record becomes the failure placeholder until it is fixed.

### `audit` sink

`audit()` [redacts only the listed names](#credential-redaction-otel-sentry-and-audit-sinks). With default settings these are **still stored as given**:

- credentials under other names, such as `client_secret`, `csrfmiddlewaretoken`, `id_token`, `user[password]` or `x_password`;
- values under `code`, `key`, `session` and `sig`;
- credentials inside a string value: free text, URLs with `?token=...` or `user:password@` (also in header values such as `Referer`), or serialized JSON;
- `action`, `object_id`, and the `user_agent` column;
- `[key, value]` pairs in a list that mixes them with other items, or in a `set` or `frozenset` of pairs;
- `request.META` passed as data: `HTTP_AUTHORIZATION`, `HTTP_COOKIE` and `QUERY_STRING` get no special handling.

For a `QueryDict` such as `extra=request.POST`, one value is stored per key (the last one) at any level above `NONE`.

Ordinary ORM calls refuse to change or delete audit rows (see below), so do not count on cleaning up such a secret later. Remove secrets before calling `audit()`, or add the exact name of a payload field to `EXTRA_DROP_HEADERS`.

**Immutability is a guard against ordinary ORM calls, not a guarantee.** `save()` and `delete()` on a loaded row, and `update()`, `bulk_update()` and `delete()` through `AuditLog.objects`, raise `AuditLogImmutableError`, and the admin does not allow changes or deletes. Rows can still be changed or removed, for example by `update()` and `delete()` on `AuditLog._base_manager`, by `bulk_create(..., update_conflicts=True)`, by deleting the actor's user (`on_delete=SET_NULL` clears `actor`), and by direct SQL. If you need more, also restrict `UPDATE` and `DELETE` on the table in the database (note that `SET_NULL` is itself an `UPDATE`).

### Wagtail extra on older Python

As of 2026-11-28, installing `django-observe-kit[wagtail]` resolves releases with published security advisories on some interpreters, because the patched releases do not support them: Python 3.10 gets a vulnerable `pillow` and `soupsieve`, and Python 3.11 before 3.11.5 gets a vulnerable `soupsieve`. Use **Python 3.11.5 or later** with the `wagtail` extra.

## Related

- [`docs/configuration.md`](configuration.md) — all `OBSERVE_KIT` keys, including `PII_LEVEL`, `PII_LEVELS`, `PII_HASH_SALT`, and the `EXTRA_*` overrides
- [`docs/middleware.md`](middleware.md) — where sanitization runs in the request lifecycle
- `src/observe_kit/pii_rules.py`, `src/observe_kit/conf.py` — implementation
