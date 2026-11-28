"""``audit()`` stores a ``QueryDict`` as scalars (last value per key), as it did before #272."""

from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Any

import pytest
from django.http import QueryDict

from observe_kit.audit.models import AuditLog
from observe_kit.audit.utils import audit
from observe_kit.context import reset_request_context
from observe_kit.pii_rules import PiiConfig, get_pii_config, set_pii_config

QUERY = (
    "password=old-secret&password=new-secret&email=alice@example.com&n=1"
    "&phone=0812345678&tag=a&tag=b&ip=10.0.0.1"
)


@pytest.fixture(autouse=True)
def _isolate() -> Any:
    reset_request_context()
    original = get_pii_config()
    yield
    set_pii_config(original)
    reset_request_context()


def _stored(level: str, field: str, value: Any) -> Any:
    set_pii_config(PiiConfig(levels={"audit": level}))
    entry: AuditLog = audit(action="x", **{field: value})
    entry.refresh_from_db()
    return entry.extra if field == "extra" else entry.extra[f"_{field}"]


@pytest.mark.parametrize("field", ["extra", "before", "after"])
@pytest.mark.parametrize("level", ["BASIC", "SENSITIVE"])
def test_querydict_stores_scalars_with_field_rules(level: str, field: str) -> None:
    ip = "10.0.0.1" if level == "BASIC" else hashlib.sha256(b"10.0.0.1").hexdigest()
    assert _stored(level, field, QueryDict(QUERY)) == {
        "password": "[Filtered]",
        "email": "a***@example.com",
        "n": "1",
        "phone": "08***",
        "tag": "b",
        "ip": ip,
    }


def test_none_level_extra_is_unchanged() -> None:
    # As before #272: at NONE ``extra`` keeps every value of every key.
    assert _stored("NONE", "extra", QueryDict("n=1&tag=a&tag=b")) == {"n": ["1"], "tag": ["a", "b"]}


@pytest.mark.parametrize("field", ["before", "after"])
def test_none_level_snapshots_are_unchanged(field: str) -> None:
    assert _stored("NONE", field, QueryDict("n=1&tag=a&tag=b")) == {"n": "1", "tag": "b"}


def test_querydict_is_not_mutated() -> None:
    query = QueryDict(QUERY, mutable=True)
    _stored("BASIC", "extra", query)
    assert query == QueryDict(QUERY, mutable=True)


def test_non_dict_mapping_extra_is_still_sanitized() -> None:
    extra = MappingProxyType({"password": "s3cr3t", "email": "alice@example.com"})
    assert _stored("BASIC", "extra", extra) == {
        "password": "[Filtered]",
        "email": "a***@example.com",
    }
