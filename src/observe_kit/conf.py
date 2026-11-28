"""Project-wide defaults and constants."""

from typing import Dict, Optional


def cgi_header_name(key: str) -> Optional[str]:
    """Return the request-header name for a CGI/WSGI ``HTTP_*`` ``META`` key.

    ``HTTP_X_FORWARDED_FOR`` → ``x-forwarded-for``. Non-``HTTP_`` keys (e.g.
    ``QUERY_STRING``, ``REMOTE_ADDR``) return ``None``. Shared so the OTel
    middleware's header extraction and the Sentry env scrubber can't drift.
    """
    if key.startswith("HTTP_"):
        return key[5:].replace("_", "-").lower()
    return None


DEFAULT_PII_LEVEL = "BASIC"
DROP_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "x-access-token"}
MASK_FIELDS = {"email", "phone"}
HASH_FIELDS = {"user-agent", "ip"}

# Query-string keys that routinely carry credentials, shared by the ``otel`` and
# ``sentry`` sinks, which redact their values by default at any PII level above NONE.
# Covers the OTel HTTP semconv default redaction keys plus common app-level secrets.
# Matching is case-insensitive (deliberately stricter than the spec).
QUERY_SECRET_KEYS = frozenset(
    {
        "token", "password", "passwd", "secret", "api_key", "apikey", "access_token",
        "refresh_token", "signature", "sig", "code", "session", "key",
        "x-amz-signature", "x-amz-credential", "x-amz-security-token", "awsaccesskeyid",
        "x-goog-signature",
    }
)  # fmt: skip
# Marker for a redacted query value (OTel semconv). Bracket-free so a rescan of the
# scrubbed URL is stable.
QUERY_REDACTED = "REDACTED"
# Marker for a redacted body / databag value; matches Sentry's own ``EventScrubber``.
BODY_REDACTED = "[Filtered]"
# The subset that is unambiguous outside a URL query: a request-body field called
# ``code`` / ``key`` / ``session`` / ``sig`` is usually ordinary data.
BODY_SECRET_KEYS = QUERY_SECRET_KEYS - {"code", "key", "session", "sig"}

# Default per-sink PII levels
DEFAULT_PII_LEVELS: Dict[str, str] = {
    "logs": DEFAULT_PII_LEVEL,
    "otel": DEFAULT_PII_LEVEL,
    "sentry": DEFAULT_PII_LEVEL,
    "audit": DEFAULT_PII_LEVEL,
}

# PII sink names
PII_SINK_LOGS = "logs"
PII_SINK_OTEL = "otel"
PII_SINK_SENTRY = "sentry"
PII_SINK_AUDIT = "audit"

# Body logging prevention
# Never log request or response bodies to prevent PII exposure
BODY_LOG_WARNING = "[BODY_OMITTED] Request/response bodies are never logged to prevent PII exposure"

# DB query tracking defaults are resolved via observe_kit.settings.get_observe_kit_settings().
