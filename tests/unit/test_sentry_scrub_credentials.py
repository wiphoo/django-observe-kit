"""The Sentry sink redacts credential values by default (shared set with the OTel sink)."""

from __future__ import annotations

import json
from typing import Any, Dict, List
from urllib.parse import quote

import pytest
import sentry_sdk

from observe_kit.conf import QUERY_SECRET_KEYS
from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config

SECRET = "s3cretVALUE9"
_QUERY = f"token={SECRET}a&password={SECRET}b&access_token={SECRET}c&api_key={SECRET}d"
_URL = f"https://h.test/p?{_QUERY}"


def _scrub(event: Dict[str, Any], **kwargs: Any) -> str:
    return json.dumps(config.scrub_event(event, **kwargs), default=str)


@pytest.mark.parametrize("key", sorted(QUERY_SECRET_KEYS))
@pytest.mark.parametrize("level", [PiiLevel.BASIC, PiiLevel.SENSITIVE])
def test_every_shared_key_is_redacted_in_a_url(key: str, level: PiiLevel) -> None:
    out = config.scrub_event(
        {"request": {"url": f"https://h.test/p?{key.upper()}={SECRET}&page=2"}}, level=level
    )
    url = out["request"]["url"]
    assert SECRET not in url
    assert f"{key.upper()}=REDACTED" in url
    assert "page=2" in url


@pytest.mark.parametrize(
    "event",
    [
        {"request": {"url": _URL}},
        {"request": {"query_string": _QUERY}},
        {"request": {"query_string": {"token": SECRET, "password": [SECRET, SECRET + "2"]}}},
        {"request": {"query_string": {"token": {"nested": SECRET}}}},
        {"request": {"query_string": [["token", SECRET], ["api_key", [SECRET]]]}},
        {"request": {"query_string": (("password", SECRET),)}},
        {"request": {"query_string": {f"token={SECRET}": ""}}},
        {"request": {"query_string": {"b'token'": SECRET}}},
        {"request": {"headers": {"Referer": _URL, "X-Next": f"/p?token={SECRET}"}}},
        {"request": {"headers": [["Referer", _URL]]}},
        {"request": {"env": {"QUERY_STRING": _QUERY, "HTTP_REFERER": _URL}}},
        {"request": {"env": {"REQUEST_URI": f"/p?{_QUERY}"}}},
        {"request": {"data": _QUERY}},
        {"request": {"data": _QUERY.encode()}},
        {"request": {"data": {"password": SECRET, "u": {"access_token": SECRET}}}},
        {"request": {"data": [["password", SECRET], ["api_key", SECRET]]}},
        {"request": {"data": (["token", SECRET],)}},
        {"request": {"data": [{"refresh_token": [SECRET]}]}},
        {"request": {"data": json.dumps({"client": {"secret": SECRET}})}},
        {"request": {"data": json.dumps([["password", SECRET]])}},
        {"request": {"fragment": f"access_token={SECRET}"}},
        {"request": {"url": f"https://h.test/cb#access_token={SECRET}"}},
        {"transaction": _URL},
        {"transaction": f"/p?{_QUERY}"},
        {"message": f"GET {_URL} failed"},
        {"message": f"GET /p?{_QUERY} failed"},
        {"message": f"retry callback?token={SECRET}"},
        {"logentry": {"message": "u=%s", "params": [_URL]}},
        {"logentry": {"formatted": f"u={_URL}"}},
        {"exception": {"values": [{"value": f"HTTPError for url: {_URL}"}]}},
        {"breadcrumbs": {"values": [{"message": _URL, "data": {"url": _URL}}]}},
        {"breadcrumbs": [{"data": {"url": "https://h.test/p", "http.query": _QUERY}}]},
        {"breadcrumbs": [{"data": {"http.fragment": f"access_token={SECRET}"}}]},
        {"spans": [{"op": "http.client", "description": f"GET {_URL}"}]},
        {"spans": [{"data": {"http.url": _URL, "url.full": _URL, "url.query": _QUERY}}]},
        {"spans": [{"data": {"http.query": f"?{_QUERY}"}}]},
        {"spans": [{"data": {"url.fragment": f"access_token={SECRET}"}}]},
        {"spans": [{"data": [["http.fragment", f"#id_token={SECRET}"]]}]},
        {"tags": {"next": _URL}},
        {"extra": {"callback": _URL}},
        # percent-encoded keys / delimiters and nested redirects
        {"request": {"url": f"https://h.test/p?%74oken={SECRET}"}},
        {"request": {"url": f"https://h.test/p?to%256ben={SECRET}"}},
        {"request": {"url": f"https://h.test/p?token%3D{SECRET}"}},
        {"request": {"url": f"https://h.test/p%3Ftoken%3D{SECRET}"}},
        {"request": {"url": f"https://h.test/login?next=/p%3Ftoken%3D{SECRET}"}},
        {"request": {"url": f"https://h.test/login?next={quote(_URL, safe='')}"}},
        {"request": {"url": f"https://h.test/login?next={quote(quote(_URL, safe=''), safe='')}"}},
        {"request": {"query_string": {"next": f"/p?password={SECRET}"}}},
        {"message": f"redirect:/p?token={SECRET}"},
        {"message": f"GET /s%3Ftoken%3D{SECRET} HTTP/1.1"},
        {"message": f"GET /s?token%3D{SECRET}"},
        {"message": f"go to {quote(_URL, safe='')} now"},
    ],
)
def test_credentials_never_reach_sentry(event: Dict[str, Any]) -> None:
    assert SECRET not in _scrub(event)


@pytest.mark.parametrize("key", ["code", "key", "session", "sig"])
def test_ambiguous_names_are_only_redacted_in_query_context(key: str) -> None:
    out = config.scrub_event(
        {
            "request": {"url": f"https://h.test/p?{key}=abc123", "data": {key: "abc123"}},
            "extra": {key: "abc123"},
            "contexts": {"c": {key: "abc123"}},
            "tags": {key: "abc123"},
        }
    )
    assert "abc123" not in out["request"]["url"]
    assert out["request"]["data"] == {key: "abc123"}
    assert out["extra"] == {key: "abc123"}
    assert out["contexts"] == {"c": {key: "abc123"}}
    assert out["tags"] == {key: "abc123"}


def test_dict_keys_outside_request_data_are_left_alone() -> None:
    out = config.scrub_event({"extra": {"token": "abc123"}, "contexts": {"c": {"secret": "x1"}}})
    assert out == {"extra": {"token": "abc123"}, "contexts": {"c": {"secret": "x1"}}}


def test_empty_and_unrelated_values_are_unchanged() -> None:
    url = "https://h.test/p?token=&page=2&tokens=3#section"
    assert config.scrub_event({"request": {"url": url, "data": {"password": ""}}}) == {
        "request": {"url": url, "data": {"password": ""}}
    }


def test_level_none_is_a_no_op() -> None:
    assert SECRET in _scrub({"request": {"url": _URL}}, level=PiiLevel.NONE)


def test_operator_rules_win_over_the_default() -> None:
    event = {
        "request": {
            "url": f"https://h.test/p?token={SECRET}&password={SECRET}&secret={SECRET}",
            "data": {"password": SECRET, "token": SECRET, "secret": SECRET},
        }
    }
    out = config.scrub_event(
        event,
        level=PiiLevel.SENSITIVE,
        hash_salt="salt",
        extra_drop=frozenset({"token"}),
        extra_mask=frozenset({"password"}),
        extra_hash=frozenset({"secret"}),
    )
    digest = config._hash_value(SECRET, "salt")
    assert out["request"]["url"] == f"https://h.test/p?password=s3%2A%2A%2A&secret={digest}"
    assert out["request"]["data"] == {"password": "s3***", "secret": digest}


@pytest.mark.parametrize(
    "shape",
    [lambda body: body, lambda body: [[k, v] for k, v in body.items()], lambda body: [body]],
)
def test_operator_hash_on_a_body_container_sees_the_original_subtree(shape: Any) -> None:
    def digest(password: str) -> str:
        profile = {"password": password, "id": 3}
        out = _scrub(
            {"request": {"data": shape({"profile": profile})}},
            level=PiiLevel.SENSITIVE,
            hash_salt="salt",
            extra_hash=frozenset({"profile"}),
        )
        assert password not in out
        assert config._hash_value(str(profile), "salt") in out
        return out

    assert digest("hunter2") != digest("hunter3")


def test_operator_hash_on_a_json_string_body_container_sees_the_original_subtree() -> None:
    profile = {"password": SECRET, "id": 3}
    out = _scrub(
        {"request": {"data": json.dumps({"profile": profile})}},
        level=PiiLevel.SENSITIVE,
        hash_salt="salt",
        extra_hash=frozenset({"profile"}),
    )
    assert SECRET not in out
    assert config._hash_value(str(profile), "salt") in out


@pytest.mark.parametrize("rule", ["extra_drop", "extra_mask", "extra_hash"])
@pytest.mark.parametrize("key", ["profile", "url", "message", "params", "http.query"])
@pytest.mark.parametrize("level", [PiiLevel.BASIC, PiiLevel.SENSITIVE])
def test_operator_ruled_body_container_never_leaks_credentials(
    rule: str, key: str, level: PiiLevel
) -> None:
    # Leaving a ruled subtree to its rule must not let a credential through, including
    # under walk-managed keys and for a hash rule that is inactive at BASIC.
    body = {key: {"password": SECRET}}
    for data in (body, [[key, body[key]]], json.dumps(body)):
        out = _scrub(
            {"request": {"data": data}}, level=level, hash_salt="s", **{rule: frozenset({key})}
        )
        assert SECRET not in out


def test_hash_rule_inactive_at_basic_still_redacts() -> None:
    out = _scrub({"request": {"url": _URL}}, extra_hash=frozenset({"token", "password"}))
    assert SECRET not in out


def test_second_pass_is_stable() -> None:
    once = config.scrub_event({"message": f"GET {_URL}", "request": {"url": _URL}})
    assert config.scrub_event(json.loads(json.dumps(once))) == once


def test_real_sdk_envelopes_carry_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default settings, real sentry_sdk pipeline: transaction, span and message."""
    sent: List[str] = []
    real_init = sentry_sdk.init

    class _Capture(sentry_sdk.transport.Transport):
        def capture_envelope(self, envelope: Any) -> None:
            sent.append(envelope.serialize().decode())

    def _init(**kwargs: Any) -> Any:
        kwargs["integrations"] = []
        return real_init(
            transport=_Capture(),
            default_integrations=False,
            auto_enabling_integrations=False,
            **kwargs,
        )

    monkeypatch.setattr(config.sentry_sdk, "init", _init)
    try:
        config.init_sentry("https://k@sentry.invalid/1", "test", traces_sample_rate=1.0)
        with sentry_sdk.start_transaction(name=_URL, op="http.server"):
            with sentry_sdk.start_span(op="http.client", name=f"GET {_URL}") as span:
                span.set_data("http.url", _URL)
        sentry_sdk.capture_message(f"calling {_URL}")
        sentry_sdk.flush()
    finally:
        real_init()  # detach the capture client

    payload = "".join(sent)
    assert '"transaction"' in payload and "calling https://h.test/p" in payload
    assert SECRET not in payload
