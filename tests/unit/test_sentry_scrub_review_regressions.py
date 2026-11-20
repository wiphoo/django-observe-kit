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


def test_relative_and_rootless_urls_continue_past_parens_and_commas() -> None:
    for text in ("GET /search?x=f(a)&phone=0812345678", "callback?x=a,b&phone=0812345678"):
        assert "0812345678" not in _run({"message": text})["message"]


def test_byte_key_params_subtree_honours_drop_rule() -> None:
    event = {"extra": {"params": "safe", "b'params'": ["supersecret"]}}
    out = config.scrub_event(event, None, PiiLevel.BASIC, "", extra_drop=frozenset({"params"}))
    assert "supersecret" not in str(out)


def test_userinfo_with_comma_is_fully_redacted() -> None:
    msg = _run({"message": "https://alice:supersecret,more@host/p"})["message"]
    assert "alice" not in msg and "supersecret" not in msg


def test_credentialed_url_hashes_query_once_like_request_url() -> None:
    url = "https://u:p@h/p?ip=192.0.2.1"
    out = _run({"message": url, "request": {"url": url}}, PiiLevel.SENSITIVE)
    assert out["message"].split("?")[-1] == out["request"]["url"].split("?")[-1]


def test_bracketed_ipv6_url_without_path_keeps_query_in_candidate() -> None:
    msg = _run({"message": "see https://[2001:db8::1]?phone=0812345678 now"})["message"]
    assert "0812345678" not in msg


def test_byte_key_alias_in_request_headers_and_query_string() -> None:
    bearer = {"authorization": "safe", "b'authorization'": "Bearer supersecret"}
    out = _run({"request": {"headers": dict(bearer), "query_string": dict(bearer)}})["request"]
    assert "supersecret" not in str(out)
    out = _run({"request": {"query_string": {"phone": "1", "b'phone'": "0812345678"}}})["request"]
    assert "0812345678" not in str(out)


def test_apostrophe_in_userinfo_is_fully_redacted() -> None:
    msg = _run({"message": "https://alice:supersecret:part'word@host/p"})["message"]
    assert "alice" not in msg and "supersecret" not in msg


def test_encoded_email_with_special_local_part_is_fully_masked() -> None:
    msg = _run({"message": "contact alice.smith!tag%40example.com"})["message"]
    assert "alice" not in msg and "tag" not in msg


def test_deep_encoded_email_not_hidden_by_another_masked_address() -> None:
    note = _run({"extra": {"note": "alice@example.com,bob%25252540example.com"}})["extra"]["note"]
    assert "alice" not in note and "bob" not in note


def test_masked_value_with_benign_percent_is_not_redacted() -> None:
    note = _run({"extra": {"note": "alice@example.com,progress=100%25"}})["extra"]["note"]
    assert note.startswith("a***@example.com") and "progress=100" in note
