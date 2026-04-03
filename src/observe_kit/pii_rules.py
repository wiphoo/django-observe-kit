from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, MutableMapping, Optional, Set

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


_MASK_LEVELS: frozenset[PiiLevel] = frozenset({PiiLevel.BASIC, PiiLevel.SENSITIVE})


def _mask_value(value: str) -> str:
    if not value:
        return value
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:1]}***@{domain}" if domain else "***"
    return value[:2] + "***" if len(value) > 2 else "***"


def _hash_value(value: str, salt: str = "") -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()


def _effective_sets(
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
) -> tuple[Set[str], Set[str], Set[str]]:
    drop = DROP_HEADERS | (extra_drop or frozenset())
    mask = MASK_FIELDS | (extra_mask or frozenset())
    hsh = HASH_FIELDS | (extra_hash or frozenset())
    return drop, mask, hsh


def _sanitize_mapping(
    mapping: Mapping[str, str],
    level: PiiLevel,
    drop: Set[str],
    mask: Set[str],
    hsh: Set[str],
    hash_salt: str,
) -> MutableMapping[str, str]:
    cleaned: MutableMapping[str, str] = {}
    for key, value in mapping.items():
        key_lower = str(key).lower()
        if level != PiiLevel.NONE and key_lower in drop:
            continue
        if level in _MASK_LEVELS and key_lower in mask:
            cleaned[key] = _mask_value(str(value))
        elif level == PiiLevel.SENSITIVE and key_lower in hsh:
            cleaned[key] = _hash_value(str(value), hash_salt)
        else:
            cleaned[key] = value
    return cleaned


def sanitize_headers(
    headers: Mapping[str, str],
    level: PiiLevel,
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
    hash_salt: str = "",
) -> MutableMapping[str, str]:
    drop, mask, hsh = _effective_sets(extra_drop, extra_mask, extra_hash)
    return _sanitize_mapping(headers, level, drop, mask, hsh, hash_salt)


def sanitize_query_params(
    params: Mapping[str, str],
    level: PiiLevel,
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
    hash_salt: str = "",
) -> MutableMapping[str, str]:
    drop, mask, hsh = _effective_sets(extra_drop, extra_mask, extra_hash)
    return _sanitize_mapping(params, level, drop, mask, hsh, hash_salt)


def sanitize_body(
    body: Any,
    level: PiiLevel,
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
    hash_salt: str = "",
) -> Any:
    """Recursively sanitize a parsed JSON body (dict/list) according to PII rules.

    Operates on the already-parsed Python structure, not raw bytes. Only dicts
    and lists are traversed; scalar values are returned as-is unless their
    *parent key* matches a PII field name.
    """
    if level == PiiLevel.NONE:
        return body
    drop, mask, hsh = _effective_sets(extra_drop, extra_mask, extra_hash)
    return _sanitize_node(body, level, drop, mask, hsh, hash_salt)


def _sanitize_node(
    node: Any, level: PiiLevel, drop: Set[str], mask: Set[str], hsh: Set[str], hash_salt: str
) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            key_lower = str(key).lower()
            if key_lower in drop:
                continue
            if level in _MASK_LEVELS and key_lower in mask:
                result[key] = _mask_value(str(value)) if isinstance(value, str) else "***"
            elif level == PiiLevel.SENSITIVE and key_lower in hsh:
                result[key] = _hash_value(str(value), hash_salt)
            else:
                result[key] = _sanitize_node(value, level, drop, mask, hsh, hash_salt)
        return result
    if isinstance(node, list):
        return [_sanitize_node(item, level, drop, mask, hsh, hash_salt) for item in node]
    return node
