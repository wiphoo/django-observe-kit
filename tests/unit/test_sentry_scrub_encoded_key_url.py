"""A query key that is itself a URL / query (``/p?x=y&phone``) must be scrubbed as one.

The key's ``?`` / ``#`` / ``&`` / ``=`` may be percent-encoded at any depth; the pair's
value then completes the key's last parameter (``…&phone`` + ``0812345678``). Dict and
pair-list ``query_string`` keys that hold the secret itself (``phone=0812345678``) and an
absolute ``request.url`` whose value starts with an encoded query follow the same rule.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Dict
from urllib.parse import quote, unquote, unquote_plus

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry.config import scrub_event
from observe_kit.sentry.scrub.decode import MAX_DECODE_PASSES

PHONE = "0812345678"
OTHER = "0899999999"


def _scrub(event: Dict[str, Any]) -> Dict[str, Any]:
    return scrub_event(copy.deepcopy(event), None, level=PiiLevel.SENSITIVE, hash_salt="s")


def _decoded(text: str) -> str:
    for _ in range(10):
        text = unquote(text)
    return text


def _assert_clean(out: Any, *secrets: str) -> None:
    for secret in secrets or (PHONE,):
        assert secret not in repr(out)
        assert secret not in _decoded(repr(out))


def _nest(text: str, depth: int) -> str:
    if depth == 0:
        return text
    encoded = "".join(f"%{ord(ch):02X}" for ch in text)
    for _ in range(depth - 1):
        encoded = quote(encoded, safe="")
    return encoded


WRAPS = {
    "url": lambda k, v: {"request": {"url": f"https://host.test/x?{k}={v}"}},
    "query_string": lambda k, v: {"request": {"query_string": f"{k}={v}"}},
    "message": lambda k, v: {"message": f"GET https://host.test/x?{k}={v} failed"},
    "referer": lambda k, v: {"request": {"headers": {"Referer": f"https://h.test/x?{k}={v}"}}},
}


@pytest.mark.parametrize("depth", range(1, MAX_DECODE_PASSES))
@pytest.mark.parametrize("delim", ["?", "#"])
@pytest.mark.parametrize("wrap", sorted(WRAPS))
def test_url_shaped_key_value_is_scrubbed(wrap: str, delim: str, depth: int) -> None:
    key = _nest(f"/p{delim}access_token=supersecret&phone", depth)
    out = _scrub(WRAPS[wrap](key, PHONE))
    _assert_clean(out)
    assert _scrub(out) == out  # idempotent
    if wrap == "query_string":
        assert not out["request"]["query_string"].endswith("=")  # no stray ``=``


@pytest.mark.parametrize("depth", range(1, MAX_DECODE_PASSES))
@pytest.mark.parametrize("delim", ["?", "#"])
def test_url_shaped_key_in_dict_and_pair_list(delim: str, depth: int) -> None:
    key = _nest(f"/p{delim}access_token=supersecret&phone", depth)
    for qs in ({key: PHONE}, [[key, PHONE]], {key: [PHONE, PHONE]}, [[key, [PHONE]]]):
        out = _scrub({"request": {"query_string": qs}})
        _assert_clean(out)
        assert _scrub(out) == out


@pytest.mark.parametrize(
    "qs",
    [
        {"phone=0812345678": ""},
        {"x&phone=0812345678": "1"},
        [["phone=0812345678", ""]],
        {"phone%3D0812345678": ""},
        {"phone=0812345678": ["", ""]},
    ],
)
def test_dict_key_holding_the_secret(qs: Any) -> None:
    out = _scrub({"request": {"query_string": qs}})
    _assert_clean(out)
    assert _scrub(out) == out


@pytest.mark.parametrize("depth", range(0, MAX_DECODE_PASSES))
def test_absolute_url_value_led_by_encoded_query(depth: int) -> None:
    value = _nest("?phone=", depth) + PHONE + "%20ok"
    out = _scrub({"request": {"url": f"https://h.test/s?x={value}&phone={OTHER}"}})
    _assert_clean(out, PHONE, OTHER)
    assert _scrub(out) == out
    # the same text as free text is scrubbed too
    _assert_clean(_scrub({"message": f"/s?x={value}&phone={OTHER}"}), PHONE, OTHER)


@pytest.mark.parametrize(
    "query",
    [
        "q=a%2526b",
        "p=100%25",
        "dG9rZW4%3D",
        "q=%3Fwhat%20is%20this",  # leading ``?`` but no ``=``
        "a=1&b=2",
    ],
)
def test_benign_controls_unchanged(query: str) -> None:
    for event in (
        {"request": {"query_string": query}},
        {"request": {"url": f"https://h.test/p?{query}"}},
    ):
        out = _scrub(event)
        got = out["request"].get("query_string") or out["request"]["url"].split("?", 1)[1]
        assert unquote_plus(got.rstrip("=")) == unquote_plus(query.rstrip("="))


def test_benign_dict_keys_unchanged() -> None:
    qs = {"a=b": "x", "tag%5B%5D": "y", "q?": "z"}
    assert _scrub({"request": {"query_string": qs}})["request"]["query_string"] == qs


def test_scrubbing_stays_linear() -> None:
    def run(n: int) -> float:
        key = _nest("/p?" + "a=b&" * n + "phone", 2)
        event = {
            "request": {"url": f"https://h.test/x?{key}={PHONE}", "query_string": {key: PHONE}}
        }
        start = time.perf_counter()
        _scrub(event)
        return time.perf_counter() - start

    run(10)
    small, large = run(200), run(1600)
    assert large < max(small * 40, 3.0)
