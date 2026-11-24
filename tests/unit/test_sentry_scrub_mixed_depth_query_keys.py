"""Field rules must match a query key that is percent-encoded deeper than its pair.

A key name (``ph%256fne``) or the ``=`` / ``&`` that delimits it (``phone%3D…``,
``x%26phone=…``) encoded one or more layers deeper than the rest of the query hid
the pair from the key-based field rules, while a consumer that decodes once more
still read ``phone=0812345678``.
"""

from __future__ import annotations

import copy
from typing import Any, Dict
from urllib.parse import quote, unquote

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry.config import scrub_event
from observe_kit.sentry.scrub.decode import MAX_DECODE_PASSES

PHONE = "0812345678"
MASKED = "08%2A%2A%2A"  # ``08***`` as ``urlencode`` re-emits it


def _scrub(event: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return scrub_event(
        copy.deepcopy(event), None, level=PiiLevel.SENSITIVE, hash_salt="s", **kwargs
    )


def _fully_decoded(text: str) -> str:
    for _ in range(10):
        text = unquote(text)
    return text


def _assert_no_phone(out: Any) -> None:
    assert PHONE not in repr(out)
    assert PHONE not in _fully_decoded(repr(out))  # an encoded survivor is still a leak


def _nest(text: str, depth: int) -> str:
    """``text`` percent-encoded ``depth`` layers deep (every character at depth >= 1)."""
    if depth == 0:
        return text
    encoded = "".join(f"%{ord(ch):02X}" for ch in text)
    for _ in range(depth - 1):
        encoded = quote(encoded, safe="")
    return encoded


# depth 0..6 is within the decode budget (``parse_qsl`` decodes one layer, the seam
# MAX_DECODE_PASSES more); 7 and 8 exhaust it and must be redacted, not passed through.
DEPTHS = range(MAX_DECODE_PASSES + 4)

KEY_VARIANTS = {
    "whole-key": lambda d: f"{_nest('phone', d)}={PHONE}",
    "one-letter": lambda d: f"ph{_nest('o', d)}ne={PHONE}",
    "equals": lambda d: f"phone{_nest('=', d)}{PHONE}",
    "ampersand": lambda d: f"x{_nest('&', d)}phone={PHONE}",
}


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("variant", sorted(KEY_VARIANTS))
@pytest.mark.parametrize(
    "wrap",
    [
        lambda q: {"request": {"query_string": q}},
        lambda q: {"request": {"url": f"https://host.test/p?{q}"}},
        lambda q: {"request": {"headers": {"Referer": f"https://host.test/p?{q}"}}},
        lambda q: {"request": {"env": {"QUERY_STRING": q}}},
        lambda q: {"request": {"data": q}},
        lambda q: {"message": f"GET /p?{q} failed"},
        lambda q: {"logentry": {"message": "%s", "params": [f"s?{q}"]}},
        # hidden query in the path, and a nested redirect value
        lambda q: {"request": {"url": f"https://host.test/p{quote('?' + q, safe='')}"}},
        lambda q: {"request": {"query_string": f"next={quote('/s?' + q, safe='')}"}},
    ],
    ids=[
        "query_string",
        "url",
        "referer",
        "env",
        "body",
        "message",
        "logentry-param",
        "hidden-path-query",
        "nested-redirect",
    ],
)
def test_deeper_encoded_key_is_scrubbed(wrap: Any, variant: str, depth: int) -> None:
    event = wrap(KEY_VARIANTS[variant](depth))
    if event == {"logentry": {"message": "%s", "params": [f"s?x&phone={PHONE}"]}}:
        # Nothing is encoded here: the rootless free-text locator does not accept a
        # literal ``&`` in the first key. A separate, pre-existing locator gap.
        pytest.xfail("literal `s?x&phone=…` in free text is not located (not an encoding gap)")
    out = _scrub(event)
    _assert_no_phone(out)
    again = _scrub(out)
    _assert_no_phone(again)
    # Idempotent — except past the decode cap, where the field is redacted and a
    # ``[Filtered]`` placeholder is itself re-encoded when re-parsed as a query.
    if "Filtered" not in repr(out):
        assert again == out


@pytest.mark.parametrize(
    "event, expected",
    [
        (
            {"request": {"url": "https://host.test/p%3Fphone%253D0812345678"}},
            {"request": {"url": f"https://host.test/p?phone={MASKED}"}},
        ),
        (
            {"request": {"url": "https://host.test/s?phone%3D0812345678"}},
            {"request": {"url": f"https://host.test/s?phone={MASKED}"}},
        ),
        (
            {"request": {"url": "https://host.test/p?ph%256fne=0812345678"}},
            {"request": {"url": f"https://host.test/p?ph%256fne={MASKED}"}},
        ),
        (
            {"request": {"query_string": "x%26phone=0812345678"}},
            {"request": {"query_string": f"x=&phone={MASKED}"}},
        ),
        (
            {"logentry": {"message": "%s", "params": ["s?phone%3D0812345678"]}},
            {"logentry": {"message": "%s", "params": [f"s?phone={MASKED}"]}},
        ),
        # control: uniform depth already worked
        (
            {"request": {"url": "https://host.test/p%253Fphone%253D0812345678"}},
            {"request": {"url": f"https://host.test/p?phone={MASKED}"}},
        ),
    ],
)
def test_reported_repros(event: Dict[str, Any], expected: Dict[str, Any]) -> None:
    out = _scrub(event)
    assert out == expected
    assert _scrub(out) == out


@pytest.mark.parametrize(
    "query_string",
    [
        {"ph%6fne": PHONE},
        {"ph%256fne": [PHONE, PHONE]},
        [["ph%6fne", PHONE]],
        [["%70%68%6F%6E%65", [PHONE]]],
    ],
)
def test_structured_query_string_key_is_decoded_for_rule_matching(query_string: Any) -> None:
    out = _scrub({"request": {"query_string": query_string}})
    _assert_no_phone(out)
    assert _scrub(out) == out


def test_structured_query_string_key_beyond_decode_cap_is_redacted() -> None:
    key = _nest("phone", MAX_DECODE_PASSES + 1)
    out = _scrub({"request": {"query_string": {key: PHONE}}})
    assert list(out["request"]["query_string"].values()) == ["[Filtered]"]


@pytest.mark.parametrize(
    "query",
    [
        "q=a%2526b",  # deeper-encoded ``&`` inside a VALUE is data, not a separator
        "q=100%25",
        "q=a%253Db",
        "my+key=1",
        "a%5B%5D=1",
        "k%2520x=2",  # deeper-encoded key that matches no rule keeps its spelling
        "100%25=1",
        "tag=a&tag=b",
        "dG9rZW4%3D%3D=",  # bare base64 token: a hidden ``=`` with nothing to scrub
        "a%3Db=c",
        "a%26b=c",
    ],
)
def test_benign_query_is_emitted_unchanged(query: str) -> None:
    assert _scrub({"request": {"query_string": query}})["request"]["query_string"] == query
    url = f"https://host.test/p?{query}"
    assert _scrub({"request": {"url": url}})["request"]["url"] == url


def test_encoded_space_in_benign_key_is_not_redacted() -> None:
    out = _scrub({"request": {"query_string": "my%20key=1"}})
    assert out == {"request": {"query_string": "my+key=1"}}  # urlencode's spelling of the space


def test_hidden_pair_keeps_value_data_intact() -> None:
    """Re-parsing a hidden pair must not decode the pair's value a second time."""
    out = _scrub({"request": {"query_string": "alice%40b.test%26q=c%2526d%2Be%26f"}})
    assert out == {
        "request": {"query_string": "***=c%2526d%2Be%26f"}
    }  # whole key masked, value intact


@pytest.mark.parametrize(
    "text",
    [
        "see docs?page%3D2 now",
        "why?100%25 sure",
        "open /p?dG9rZW4%3D%3D please",
        "GET /p?q=a%2526b failed",
    ],
)
def test_benign_free_text_is_emitted_unchanged(text: str) -> None:
    assert _scrub({"message": text}) == {"message": text}
    assert _scrub({"logentry": {"message": "%s", "params": [text]}})["logentry"]["params"] == [text]


@pytest.mark.parametrize("key", ["token", "t%256fken", "%2574%256F%256B%2565%256E"])
def test_deeper_encoded_hash_key_is_hashed_exactly_once(key: str) -> None:
    reference = _scrub(
        {"request": {"query_string": "token=supersecret"}}, extra_hash=frozenset({"token"})
    )["request"]["query_string"]
    hashed = reference.split("=", 1)[1]
    assert "supersecret" not in reference and hashed
    for event in (
        {"request": {"query_string": f"{key}=supersecret"}},
        {"request": {"url": f"https://host.test/p?{key}=supersecret"}},
    ):
        out = _scrub(event, extra_hash=frozenset({"token"}))
        assert repr(out).count(hashed) == 1
        assert "supersecret" not in repr(out)


def test_hidden_pair_hash_key_is_hashed_exactly_once() -> None:
    reference = _scrub(
        {"request": {"url": "https://host.test/p?token=supersecret"}},
        extra_hash=frozenset({"token"}),
    )
    out = _scrub(
        {"request": {"url": "https://host.test/p?token%3Dsupersecret"}},
        extra_hash=frozenset({"token"}),
    )
    assert out == reference


@pytest.mark.parametrize("qs", ["a%26phone=0812345678", "a%3Dphone=0812345678"])
def test_operator_rule_on_whole_hidden_delimiter_key_still_applies(qs: str) -> None:
    key = unquote(qs.split("=0812", 1)[0])
    out = _scrub({"request": {"query_string": qs}}, extra_drop=frozenset({key}))
    assert out["request"]["query_string"] == ""


@pytest.mark.parametrize("sep", ["%26", "%3D"])
def test_email_key_with_hidden_delimiter_keeps_whole_key_mask(sep: str) -> None:
    out = _scrub({"request": {"query_string": f"alice{sep}bob%40example.com=1"}})
    assert "alice" not in repr(out)


def test_operator_rule_on_literal_percent_key_still_applies() -> None:
    out = _scrub(
        {"request": {"query_string": "a%2520b=supersecret"}}, extra_drop=frozenset({"a%20b"})
    )
    assert "supersecret" not in repr(out)
    out = _scrub(
        {"request": {"query_string": {"a%20b": "supersecret"}}}, extra_drop=frozenset({"a%20b"})
    )
    assert "supersecret" not in repr(out)
