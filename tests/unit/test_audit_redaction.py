"""``audit()`` must not store credential values at default settings."""

from __future__ import annotations

import copy
import hashlib
import json
from types import MappingProxyType
from typing import Any

import pytest

from observe_kit.audit.models import AuditLog
from observe_kit.audit.utils import audit
from observe_kit.conf import BODY_SECRET_KEYS
from observe_kit.context import reset_request_context
from observe_kit.pii_rules import PiiConfig, get_pii_config, set_pii_config

SECRET = "s3cr3t-value"


@pytest.fixture(autouse=True)
def _isolate() -> Any:
    reset_request_context()
    original = get_pii_config()
    set_pii_config(PiiConfig())
    yield
    set_pii_config(original)
    reset_request_context()


def _stored(entry: AuditLog) -> Any:
    entry.refresh_from_db()
    return entry.extra


def _payload() -> dict[str, Any]:
    return {
        "password": SECRET,
        "Token": SECRET,
        "nested": {"API_KEY": SECRET, "list": [{"secret": SECRET}]},
        "tuple": ({"refresh_token": SECRET},),
        "pairs": [["password", SECRET], ("access_token", SECRET), ["note", "ok"]],
    }


@pytest.mark.parametrize("field", ["extra", "before", "after"])
def test_default_settings_never_store_credentials(field: str) -> None:
    payload = _payload()
    original = copy.deepcopy(payload)

    stored = _stored(audit(action="x", **{field: payload}))

    assert SECRET not in json.dumps(stored)
    data = stored if field == "extra" else stored[f"_{field}"]
    assert data["password"] == "[Filtered]"
    assert data["pairs"][2] == ["note", "ok"]
    assert payload == original  # caller objects are never mutated


@pytest.mark.parametrize("key", sorted(BODY_SECRET_KEYS))
def test_every_shared_body_secret_key_is_redacted(key: str) -> None:
    assert _stored(audit(action="x", extra={key.upper(): SECRET})) == {key.upper(): "[Filtered]"}


@pytest.mark.parametrize("value", [["a", SECRET], {"inner": SECRET}, 12345, False])
def test_non_string_and_container_values_are_redacted_whole(value: Any) -> None:
    assert _stored(audit(action="x", extra={"password": value})) == {"password": "[Filtered]"}


def test_ambiguous_keys_empty_values_and_other_fields_are_unchanged() -> None:
    extra = {
        "code": "A1",
        "key": "colour",
        "session": "morning",
        "sig": "x",
        "password": "",
        "token": None,
        "token_count": 3,
        "email": "alice@example.com",
        "note": "password reset",
    }
    assert _stored(audit(action="x", extra=extra)) == {**extra, "email": "a***@example.com"}


def test_top_level_non_dict_mapping_extra_is_redacted() -> None:
    stored = _stored(audit(action="x", extra=MappingProxyType({"password": SECRET})))
    assert stored == {"password": "[Filtered]"}


def test_none_level_stores_values_as_given() -> None:
    set_pii_config(PiiConfig(levels={"audit": "NONE"}))
    assert _stored(audit(action="x", extra={"password": SECRET})) == {"password": SECRET}


def test_sensitive_level_redacts() -> None:
    set_pii_config(PiiConfig(levels={"audit": "SENSITIVE"}))
    assert _stored(audit(action="x", extra={"password": SECRET})) == {"password": "[Filtered]"}


def test_operator_drop_wins(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"EXTRA_DROP_HEADERS": ["password"]}):
        assert _stored(audit(action="x", extra={"password": SECRET})) == {}


def test_operator_mask_wins(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"EXTRA_MASK_FIELDS": ["password"]}):
        assert _stored(audit(action="x", extra={"password": SECRET})) == {"password": "s3***"}


@pytest.mark.parametrize("value", [SECRET, {"inner": SECRET, "token": SECRET}])
def test_operator_hash_applies_once_to_the_original_value(
    observe_kit_settings: Any, value: Any
) -> None:
    set_pii_config(PiiConfig(levels={"audit": "SENSITIVE"}))
    expected = hashlib.sha256(("salt" + str(value)).encode()).hexdigest()
    with observe_kit_settings({"EXTRA_HASH_FIELDS": ["password"], "PII_HASH_SALT": "salt"}):
        stored = _stored(audit(action="x", extra={"password": value}))
    assert stored == {"password": expected}


def test_inactive_hash_rule_still_redacts_at_basic(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"EXTRA_HASH_FIELDS": ["password"]}):
        assert _stored(audit(action="x", extra={"password": SECRET})) == {"password": "[Filtered]"}
