"""Project-wide defaults and constants."""

from typing import Dict

DEFAULT_PII_LEVEL = "BASIC"
DROP_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "x-access-token"}
MASK_FIELDS = {"email", "phone"}
HASH_FIELDS = {"user-agent", "ip"}

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

# DB tracking configuration
# Set to False to disable DB query tracking (improves performance on high-traffic sites)
ENABLE_DB_TRACKING = True
