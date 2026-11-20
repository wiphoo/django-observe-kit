"""Operator field rules on generic string keys below the top level (thread 6WW2X9 / #100)."""

from __future__ import annotations

from typing import Any, Dict

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config


def _scrub(event: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    return config.scrub_event(event, None, PiiLevel.SENSITIVE, "salt", **kw)


def test_span_generic_string_key_mask_drop_hash() -> None:
    out = _scrub(
        {"spans": [{"op": "secretop", "origin": "o", "status": "s", "description": "d"}]},
        extra_mask=frozenset({"op"}),
        extra_drop=frozenset({"origin"}),
        extra_hash=frozenset({"status"}),
    )
    span = out["spans"][0]
    assert span["op"] != "secretop"
    assert "origin" not in span
    assert span["status"] == config._hash_value("s", "salt")  # hashed exactly once


def test_masking_span_op_does_not_unredact_db_description() -> None:
    out = _scrub(
        {"spans": [{"op": "db.query", "description": "SELECT 1 WHERE p='0812345678'"}]},
        extra_mask=frozenset({"op"}),
    )
    assert out["spans"][0]["description"] == "[Filtered]"
    assert out["spans"][0]["op"] != "db.query"


def test_request_generic_string_key_rules_apply_once() -> None:
    out = _scrub(
        {"request": {"method": "POST", "api_target": "t"}},
        extra_hash=frozenset({"method"}),
        extra_drop=frozenset({"api_target"}),
    )
    assert out["request"]["method"] == config._hash_value("POST", "salt")
    assert "api_target" not in out["request"]


def test_exception_entry_and_frame_generic_keys() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {"frames": [{"filename": "/srv/alice/app.py", "lineno": 3}]},
                }
            ]
        }
    }
    out = _scrub(event, extra_hash=frozenset({"type", "filename"}))
    entry = out["exception"]["values"][0]
    assert entry["type"] == config._hash_value("ValueError", "salt")
    frame = entry["stacktrace"]["frames"][0]
    assert frame["filename"] == config._hash_value("/srv/alice/app.py", "salt")
    assert frame["lineno"] == 3


def test_generic_key_rules_leave_untouched_without_operator_rule() -> None:
    event = {"spans": [{"op": "http.client"}], "request": {"method": "GET"}}
    out = _scrub(event)
    assert out["spans"][0]["op"] == "http.client"
    assert out["request"]["method"] == "GET"
