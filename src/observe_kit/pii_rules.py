from __future__ import annotations

import hashlib
from enum import Enum
from typing import Mapping, MutableMapping

from .conf import DROP_HEADERS, HASH_FIELDS, MASK_FIELDS


class PiiLevel(str, Enum):
    NONE = "NONE"
    BASIC = "BASIC"
    SENSITIVE = "SENSITIVE"


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
