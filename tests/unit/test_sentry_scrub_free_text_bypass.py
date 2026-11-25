"""Free-text bypasses of the Sentry URL scrubber.

(a) an encoded relative URL followed by encoded whitespace (``/s%3Fphone%3D…%20ok``)
and (b) a ``:``-prefixed relative URL (``redirect:/s?phone=…``) used to reach Sentry
with the query value unmasked.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Dict
from urllib.parse import unquote

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config

_PHONE = "0812345678"
_MASKED = "08%2A%2A%2A"


def _scrub(event: Dict[str, Any]) -> Dict[str, Any]:
    out = config.scrub_event(copy.deepcopy(event), None, level=PiiLevel.SENSITIVE, hash_salt="s")
    assert out is not None
    return out


def _exc(value: str) -> Dict[str, Any]:
    return {"exception": {"values": [{"value": value}]}}


_LEAKS = [
    # (a) encoded relative URL followed by encoded whitespace
    ({"message": "/s%3Fphone%3D0812345678%20ok"}, {"message": f"/s?phone={_MASKED} ok"}),
    (
        {"message": "GET /s%3Fphone%3D0812345678%20HTTP/1.1"},
        {"message": f"GET /s?phone={_MASKED} HTTP/1.1"},
    ),
    (
        _exc("bad request line: GET%20/s%3Fphone%3D0812345678%20HTTP/1.1"),
        _exc(f"bad request line: GET%20/s?phone={_MASKED} HTTP/1.1"),
    ),
    ({"transaction": "/s%3Fphone%3D0812345678%20ok"}, {"transaction": f"/s?phone={_MASKED} ok"}),
    ({"transaction": "/s%3Fphone%3D0812345678%0Aok"}, {"transaction": f"/s?phone={_MASKED}\nok"}),
    ({"transaction": "/s%3Fphone%3D0812345678%09ok"}, {"transaction": f"/s?phone={_MASKED}\tok"}),
    # double-encoded, and a second URL token after the whitespace
    ({"message": "/s%253Fphone%253D0812345678%2520ok"}, {"message": f"/s?phone={_MASKED} ok"}),
    (
        {"message": "/a%3Fphone%3D0812345678%20/b%3Fx%3D1"},
        {"message": f"/a?phone={_MASKED} /b?x=1"},
    ),
    # (b) ``:``-prefixed relative URL in free text
    ({"message": "redirect:/s?phone=0812345678"}, {"message": f"redirect:/s?phone={_MASKED}"}),
    (
        _exc("No route for url:/s?email=x&phone=0812345678"),
        _exc(f"No route for url:/s?email=%2A%2A%2A&phone={_MASKED}"),
    ),
    (
        {"message": "GET /a?x=1 then redirect:/s?phone=0812345678 failed"},
        {"message": f"GET /a?x=1 then redirect:/s?phone={_MASKED} failed"},
    ),
    # prefixed URLs chained in one token, and after a URL the locator already sees
    (
        {"message": "a:/x?phone=0812345678,b:/y?phone=0812345678"},
        {"message": f"a:/x?phone={_MASKED},b:/y?phone={_MASKED}"},
    ),
    (
        {"message": "https://h/ok,redirect:/s?phone=0812345678"},
        {"message": f"https://h/ok,redirect:/s?phone={_MASKED}"},
    ),
    (
        {"message": "/a?x=1,redirect:/s?phone=0812345678"},
        {"message": f"/a?x=1,redirect:/s?phone={_MASKED}"},
    ),
    (
        {"message": "saved C:/Users/me?phone=0812345678"},
        {"message": f"saved C:/Users/me?phone={_MASKED}"},
    ),
    # both: encoded whitespace then a prefixed URL
    (
        {"message": "/a%3Fphone%3D0812345678%20redirect:/s?phone=0812345678"},
        {"message": f"/a?phone={_MASKED}"},  # whole tail sits inside one visible URL
    ),
    # controls that already worked
    ({"message": "/s%3Fphone%3D0812345678"}, {"message": f"/s?phone={_MASKED}"}),
    (
        {"extra": {"k": "redirect:/s?phone=0812345678"}},
        {"extra": {"k": f"redirect:/s?phone={_MASKED}"}},
    ),
]


@pytest.mark.parametrize(("event", "expected"), _LEAKS)
def test_free_text_url_query_is_masked(event: Dict[str, Any], expected: Dict[str, Any]) -> None:
    out = _scrub(event)
    assert _PHONE not in repr(out)
    assert _PHONE not in unquote(unquote(repr(out)))
    assert out == expected


# ``email=x`` is left out: the one-character email mask grows on a second scrub
# (``***`` -> ``*****``) for any URL, prefixed or not.
@pytest.mark.parametrize("event", [e for e, _ in _LEAKS if "email=x" not in repr(e)])
def test_free_text_scrub_is_idempotent(event: Dict[str, Any]) -> None:
    once = _scrub(event)
    assert _scrub(once) == once


_BENIGN = [
    "note: see/this? and time 10:30/45",
    "go to file:/docs/readme now",
    "saved to C:/Users/me/report.txt at 10:30",
    "fetch http://example.com/docs ok",
    "ratio 3:/4 and key:value, why?",
    "error: /tmp/x not found",
    "hello%20world%20ok",
    "progress=100%25%20done",
    "see %2Fdocs%3Fpage%3D1%20for%20details",
    "redirect:/docs?page=1",
]


def test_prefixed_url_nested_in_a_located_url_is_hashed_once() -> None:
    def scrub(text: str) -> str:
        out = config.scrub_event(
            {"message": text},
            None,
            level=PiiLevel.SENSITIVE,
            hash_salt="s",
            extra_hash=frozenset({"tok"}),
        )
        return str(out["message"])

    digest = scrub("/b?tok=abc").partition("tok=")[2]
    assert len(digest) == 64
    assert scrub("redirect:/b?tok=abc") == f"redirect:/b?tok={digest}"
    assert digest in unquote(scrub("GET /a?next=x:/b?tok=abc"))
    assert digest in unquote(scrub("https://h/p?next=x:/b?tok=abc"))
    # encoded and literal spellings pseudonymise identically
    assert scrub("redirect:%2Fb?tok=abc") == f"redirect:/b?tok={digest}"


def test_encoded_whitespace_inside_visible_url_keeps_query_boundaries() -> None:
    out = _scrub({"message": "https://h/%3Fphone=0812345678%20ok&phone=0899999999"})
    assert out == {"message": f"https://h/?phone={_MASKED}&phone={_MASKED}"}


@pytest.mark.parametrize("text", _BENIGN)
def test_benign_free_text_is_unchanged(text: str) -> None:
    assert _scrub({"message": text}) == {"message": text}
    assert _scrub(_exc(text)) == _exc(text)


_SCALE_CASES = {
    "encoded-space-tokens": lambda n: "/s%3Fphone%3D1%20ok%20" * (n // 22),
    "encoded-space-run": lambda n: "%3Fa%3D1" + "%20x" * (n // 4),
    "encoded-prefixed-tokens": lambda n: "%3F" + "a:/b?c=d%20" * (n // 11),
    "prefixed-tokens": lambda n: "redirect:/s?phone=1 " * (n // 20),
    "prefixed-chain": lambda n: "a:/b?c=d," * (n // 9),
    "colon-slash-run": lambda n: ":/" * (n // 2),
}


@pytest.mark.parametrize("shape", sorted(_SCALE_CASES))
def test_scrub_event_message_scales_linearly_by_shape(shape: str) -> None:
    build = _SCALE_CASES[shape]
    n = 100_000

    def timed(size: int) -> float:
        text = build(size)
        started = time.perf_counter()
        config.scrub_event({"message": text}, None)
        return time.perf_counter() - started

    half, full = timed(n // 2), timed(n)
    assert full < 2.0 or full < 3.0 * half, (half, full)


def test_encoded_whitespace_inside_relative_url_keeps_query_boundaries() -> None:
    out = _scrub({"message": "/s?x=%3Fphone=0812345678%20ok&phone=0899999999"})
    assert out == {"message": f"/s?x=%3Fphone%3D{_MASKED}+ok&phone={_MASKED}"}


def test_nested_encoded_query_under_prefixed_url_is_hashed_once() -> None:
    def scrub(text: str) -> str:
        out = config.scrub_event(
            {"message": text},
            None,
            level=PiiLevel.SENSITIVE,
            hash_salt="s",
            extra_hash=frozenset({"tok"}),
        )
        return str(out["message"])

    digest = scrub("/b?tok=abc").partition("tok=")[2]
    assert scrub("redirect:/s?next=%3Ftok%3Dabc") == f"redirect:/s?next=%3Ftok%3D{digest}"
    assert scrub("redirect:%2Fs?tok=abc") == f"redirect:/s?tok={digest}"


@pytest.mark.parametrize(
    "text",
    [
        "https://h/%3Fphone=0812345678%20ok&phone=0899999999",
        "GET /s%3Fphone%3D0812345678%20HTTP/1.1",
        "redirect:/s?phone=0812345678",
        "redirect:/s?next=%3Fphone%3D0812345678",
    ],
)
def test_scrub_is_idempotent(text: str) -> None:
    once = _scrub({"message": text})
    assert _scrub(once) == once
