from __future__ import annotations

import hashlib
from enum import Enum
from typing import Dict, Mapping, MutableMapping, Optional

from .conf import DEFAULT_PII_LEVELS, DROP_HEADERS, HASH_FIELDS, MASK_FIELDS


class PiiLevel(str, Enum):
    NONE = "NONE"
    BASIC = "BASIC"
    SENSITIVE = "SENSITIVE"


class PiiConfig:
    """Manages per-sink PII levels.

    Allows different PII sanitization levels for different observability sinks:
    - logs: For structured logging
    - otel: For OpenTelemetry spans
    - sentry: For Sentry error reporting
    - audit: For audit log entries
    """

    def __init__(self, levels: Optional[Dict[str, str]] = None):
        """Initialize PII configuration.

        Args:
            levels: Dictionary mapping sink names to PII levels.
                   Valid sinks: 'logs', 'otel', 'sentry', 'audit'
                   Valid levels: 'NONE', 'BASIC', 'SENSITIVE'
        """
        self._levels: Dict[str, PiiLevel] = {}

        # Start with defaults
        for sink, level_str in DEFAULT_PII_LEVELS.items():
            self._levels[sink] = PiiLevel(level_str)

        # Override with provided levels
        if levels:
            for sink, level_str in levels.items():
                if sink in DEFAULT_PII_LEVELS:
                    self._levels[sink] = PiiLevel(level_str)

    def get_level(self, sink: str) -> PiiLevel:
        """Get PII level for a specific sink.

        Args:
            sink: Sink name ('logs', 'otel', 'sentry', 'audit')

        Returns:
            PII level for the sink, or BASIC as fallback
        """
        return self._levels.get(sink, PiiLevel.BASIC)

    def set_level(self, sink: str, level: str) -> None:
        """Set PII level for a specific sink.

        Args:
            sink: Sink name ('logs', 'otel', 'sentry', 'audit')
            level: PII level ('NONE', 'BASIC', 'SENSITIVE')
        """
        if sink in DEFAULT_PII_LEVELS:
            self._levels[sink] = PiiLevel(level)


# Global PII configuration instance
_global_pii_config: Optional[PiiConfig] = None


def get_pii_config() -> PiiConfig:
    """Get the global PII configuration."""
    global _global_pii_config
    if _global_pii_config is None:
        _global_pii_config = PiiConfig()
    return _global_pii_config


def set_pii_config(config: PiiConfig) -> None:
    """Set the global PII configuration."""
    global _global_pii_config
    _global_pii_config = config


def _mask_value(value: str) -> str:
    if not value:
        return value
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:1]}***@{domain}" if domain else "***"
    return value[:2] + "***" if len(value) > 2 else "***"


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitize_headers(headers: Mapping[str, str], level: PiiLevel) -> MutableMapping[str, str]:
    cleaned: MutableMapping[str, str] = {}
    for key, value in headers.items():
        key_lower = key.lower()
        if level != PiiLevel.NONE and key_lower in DROP_HEADERS:
            continue
        if level in {PiiLevel.BASIC, PiiLevel.SENSITIVE} and key_lower in MASK_FIELDS:
            cleaned[key] = _mask_value(str(value))
        elif level == PiiLevel.SENSITIVE and key_lower in HASH_FIELDS:
            cleaned[key] = _hash_value(str(value))
        else:
            cleaned[key] = value
    return cleaned


def sanitize_query_params(params: Mapping[str, str], level: PiiLevel) -> MutableMapping[str, str]:
    cleaned: MutableMapping[str, str] = {}
    for key, value in params.items():
        key_lower = str(key).lower()
        if level != PiiLevel.NONE and key_lower in DROP_HEADERS:
            continue
        if level in {PiiLevel.BASIC, PiiLevel.SENSITIVE} and key_lower in MASK_FIELDS:
            cleaned[key] = _mask_value(str(value))
        elif level == PiiLevel.SENSITIVE and key_lower in HASH_FIELDS:
            cleaned[key] = _hash_value(str(value))
        else:
            cleaned[key] = value
    return cleaned
