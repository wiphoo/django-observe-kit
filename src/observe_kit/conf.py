"""Project-wide defaults and constants."""

DEFAULT_PII_LEVEL = "BASIC"
DROP_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "x-access-token"}
MASK_FIELDS = {"email", "phone"}
HASH_FIELDS = {"user-agent", "ip"}
