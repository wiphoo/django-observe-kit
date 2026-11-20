"""Regression tests for Codex review findings on #69 (one test per finding)."""

from __future__ import annotations

from typing import Any, Dict

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config


def _run(event: Dict[str, Any], level: PiiLevel = PiiLevel.BASIC) -> Dict[str, Any]:
    return config.scrub_event(event, None, level, "salt")


def test_url_in_message_hashes_query_once_like_request_url() -> None:
    url = "https://example.com/p?ip=192.0.2.1"
    out = _run({"message": url, "request": {"url": url}}, PiiLevel.SENSITIVE)
    assert out["message"] == out["request"]["url"]


def test_nested_params_hashed_once_like_other_containers() -> None:
    out = _run(
        {"extra": {"params": {"ip": "192.0.2.1"}, "other": {"ip": "192.0.2.1"}}}, PiiLevel.SENSITIVE
    )
    assert out["extra"]["params"] == out["extra"]["other"]


def test_logentry_params_hashed_once() -> None:
    out = _run(
        {
            "logentry": {"message": "x", "params": {"ip": "192.0.2.1"}},
            "extra": {"o": {"ip": "192.0.2.1"}},
        },
        PiiLevel.SENSITIVE,
    )
    assert out["logentry"]["params"] == out["extra"]["o"]


def test_byte_key_collision_with_compound_value_is_redacted() -> None:
    out = _run({"extra": {"authorization": "safe", "b'authorization'": ["Bearer supersecret"]}})
    assert "supersecret" not in str(out)


def test_single_char_single_label_email_domain_is_masked() -> None:
    assert "alice" not in _run({"message": "mail alice@x now"})["message"]


def test_rootless_url_with_tilde_key_is_scrubbed() -> None:
    msg = _run({"message": "redirect callback?filter~=x&phone=0812345678"})["message"]
    assert "0812345678" not in msg


def test_unhashable_query_pair_key_does_not_raise() -> None:
    _run({"request": {"query_string": [[[], "secret"]]}})


def test_apostrophe_in_query_value_does_not_hide_later_param() -> None:
    msg = _run({"message": "see https://host/p?x=o'clock&phone=0812345678 now"})["message"]
    assert "0812345678" not in msg


def test_prose_around_rootless_url_is_preserved() -> None:
    note = _run({"extra": {"note": "redirect callback?phone=0812345678 now"}})["extra"]["note"]
    assert note.endswith(" now") and "0812345678" not in note


def test_container_valued_query_param_is_sanitized() -> None:
    qs = {"profile": {"phone": "0812345678", "authorization": "Bearer x"}}
    out = _run({"request": {"query_string": qs}})["request"]["query_string"]
    assert "0812345678" not in str(out) and "Bearer x" not in str(out)


def test_container_query_param_gets_value_walk() -> None:
    qs = {"profile": {"url": "https://host/?phone=0812345678"}}
    out = _run({"request": {"query_string": qs}})["request"]["query_string"]
    assert "0812345678" not in str(out)


def test_apostrophe_in_relative_query_value_does_not_hide_later_param() -> None:
    msg = _run({"message": "GET /search?q=O'Reilly&phone=0812345678"})["message"]
    assert "0812345678" not in msg
    msg = _run({"message": "see callback?q=O'Reilly&phone=0812345678"})["message"]
    assert "0812345678" not in msg


def test_span_description_honours_walk_managed_field_rule() -> None:
    event = {"spans": [{"op": "http.client", "description": "GET https://h/p?message=supersecret"}]}
    out = config.scrub_event(event, None, PiiLevel.BASIC, "", extra_mask=frozenset({"message"}))
    assert "supersecret" not in out["spans"][0]["description"]


def test_nested_pair_list_query_param_is_key_aware() -> None:
    qs = {"profile": [["authorization", "Bearer supersecret"], ["phone", "0812345678"]]}
    out = _run({"request": {"query_string": qs}})["request"]["query_string"]
    assert "supersecret" not in str(out) and "0812345678" not in str(out)


def test_logentry_named_arg_compound_value_honours_field_rule() -> None:
    event = {"logentry": {"message": "m", "params": {"url": ["supersecret"]}}}
    out = config.scrub_event(event, None, PiiLevel.BASIC, "", extra_mask=frozenset({"url"}))
    assert "supersecret" not in str(out)
