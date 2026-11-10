"""Linear email scanners must return exactly the spans the old regexes did (#214).

``_REFERENCE_*`` are verbatim copies of the patterns ``pii_rules`` used before the
scanners. They are the specification: do not "improve" them.
"""

import re
import time
from typing import Callable, Dict, Iterator, List, Tuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from observe_kit.pii_rules import (
    contains_email,
    contains_encoded_email,
    find_email_spans,
    find_encoded_email_spans,
    replace_email_spans,
    replace_encoded_email_spans,
)
from observe_kit.sentry import config

_REFERENCE_EMAIL_RE = re.compile(
    r"(?:\"(?:[^\"\\]|\\.)+\"|[\w.%!$'^`{|}~+\-]+)"
    r"@(?:[\w.\-]+\.[^\W\d_]{2,}|\[[^\[\]\s]+\]|[^\W\d_]{2,})"
)
_REFERENCE_ENCODED_EMAIL_RE = re.compile(
    r"(?:[\w._+\-]|%(?!40)[0-9A-Fa-f]{2})+"
    r"%40"
    r"(?:(?:[\w\-]|%(?!40)[0-9A-Fa-f]{2})+(?:\.|%2[Ee]))+"
    r"(?:[^\W\d_]|%(?!40)[0-9A-Fa-f]{2}){2,}"
)

_DIFF = settings(max_examples=3000, deadline=None, derandomize=True)


def _ref(pattern: "re.Pattern[str]", text: str) -> List[Tuple[int, int]]:
    return [match.span() for match in pattern.finditer(text)]


def _text(atoms: List[str], max_size: int) -> "st.SearchStrategy[str]":
    return st.lists(st.sampled_from(atoms), max_size=max_size).map("".join)


# General alphabet: every boundary the scanners rely on, plus U+017F / U+212A
# (case-fold into [A-Za-z] under IGNORECASE; they must NOT matter here), non-BMP
# letters and non-ASCII digits (\w and [^\W\d_] differ on them).
_GENERAL = [
    "a", "Z", "1", "_", "é", "٣", "\U0001d49c", "ſ", "K", ".", "-", "+",
    "%", "!", "$", "'", "^", "`", "{", "|", "}", "~", "@", '"', "\\", '\\"', " ", "\n", "[",
    "]", "[1.2.3.4]", "[IPv6:::1]", "@b.co", "@bb", "@b.c", "@[1.2.3.4]", "a@bb.cc",
    '"a b"@x.yy', "%40", "%2E", "%2e", "%41", "%4", "%zz", "%25", "%2B", "a%40b.co",
    "%40bb", "x%40y.zz", "%40%40", ",", ";", "=", "&", "?", "#", "/", ":", "(", ")",
]  # fmt: skip
# Quote-heavy: escaped quotes next to closing quotes, newlines inside quotes, '@'
# inside and outside quotes. The general alphabet almost never builds these (a
# deliberately wrong quoted bound survived 60k general samples).
_QUOTE_HEAVY = [
    '"',
    '"',
    "\\",
    '\\"',
    "a",
    "b",
    " ",
    "@bb.cc",
    "@",
    "\n",
    "a@bb.cc",
    ".",
    "%",
    "é",
    "@b",
]
# Encoded-heavy: items, near-miss separators and domain pieces.
_ENCODED_HEAVY = [
    "a",
    "b",
    "1",
    "4",
    "0",
    ".",
    "-",
    "_",
    "%",
    "%40",
    "%4",
    "%2E",
    "%2e",
    "%41",
    "%25",
    "%2B",
    "%zz",
    "%40bb",
    "a%40b.co",
    "x%40y.zz",
    "%63",
    "%6f",
]
# ``%2E`` is both a domain-label item and a separator, and digit labels are not a
# valid TLD: the combination the original pattern backtracked exponentially on.
_ENCODED_AMBIGUOUS = [
    "a", "1", "_", "-", ".", "%2E", "%2e", "%41", "%31", "%40", "%4", "%zz", "%2B", "b",
    "é", "٣", "a%40", "1%2E", "%2E%2E", "..", "a.", "%40a",
]  # fmt: skip


@_DIFF
@given(text=_text(_ENCODED_AMBIGUOUS, 14))
def test_encoded_spans_match_reference_ambiguous_separator(text: str) -> None:
    assert list(find_encoded_email_spans(text)) == _ref(_REFERENCE_ENCODED_EMAIL_RE, text), text


@_DIFF
@given(text=_text(_GENERAL, 14))
def test_email_spans_match_reference(text: str) -> None:
    assert list(find_email_spans(text)) == _ref(_REFERENCE_EMAIL_RE, text), text


@_DIFF
@given(text=_text(_QUOTE_HEAVY, 16))
def test_email_spans_match_reference_quote_heavy(text: str) -> None:
    assert list(find_email_spans(text)) == _ref(_REFERENCE_EMAIL_RE, text), text


@_DIFF
@given(text=_text(_GENERAL, 14))
def test_encoded_spans_match_reference(text: str) -> None:
    assert list(find_encoded_email_spans(text)) == _ref(_REFERENCE_ENCODED_EMAIL_RE, text), text


@_DIFF
@given(text=_text(_ENCODED_HEAVY, 14))
def test_encoded_spans_match_reference_encoded_heavy(text: str) -> None:
    assert list(find_encoded_email_spans(text)) == _ref(_REFERENCE_ENCODED_EMAIL_RE, text), text


_EDGE_CASES = [
    "a@bb.cc", "a@bb.cc,b@dd.ee", "a@b.co1@c.dd", "a@b.com.evil@c.dd", "x@yy.zz@aa.bb",
    "a@localhost", "a@[192.0.2.1]", "a@[IPv6:2001:db8::1]", "a@b", "@bb.cc", "a@", "@", "a@@bb.cc",
    '"a b"@x.yy', '"a@bc"@d.co', '"a\\""@bb.cc', '"a\\"@bb.cc', '""@bb.cc', '"\\\n"@bb.cc',
    '"a\n"@bb.cc', 'x@aa.bb"c\\""@dd.ee',
    '\\"@bb.cc"@bb.cc\\""@bb.cc@a@bb.cc\n@a a@bb.cc',
    "alice!@bb.cc", "o'brien@bb.cc", "a..b@bb.cc", "é@bb.cc", "a@bb.é",
    "a@例え.テスト",
    "a%40bb.cc", "a%40b.co1%40c.dd", "a%2Bb%40bb.cc", "a%40bb%2Ecc", "a%40bb%2E%63%6f%6d",
    "a%40%40bb.cc", "%40bb.cc", "a%40", "a%4", "a%zz%40bb.cc", "%41%40bb.cc", "a%2540bb.cc",
    "a%40b.c", "x%40y.zz x%40y.zz", "a@bb.cc%40dd.ee", "ſa@bb.cc", "a@bb.cK",
    "a%40%40bb.cc%40dd.ee", "40%40bb.cc", "%40%40bb.cc",
    "a%40" + "1%2E" * 12, "a%40" + "1%2E" * 12 + "cc", "a%40b%2E.", "a%40b%2E%2Ecc", "a%40..cc",
    "a%40b%2E%2E", "a%40%2Ecc", "a%40b.%2E%2Ecc", "a%40b%2Ecc%2E1%2Edd",
]  # fmt: skip


@pytest.mark.parametrize("text", _EDGE_CASES)
def test_edge_cases_match_reference(text: str) -> None:
    assert list(find_email_spans(text)) == _ref(_REFERENCE_EMAIL_RE, text)
    assert list(find_encoded_email_spans(text)) == _ref(_REFERENCE_ENCODED_EMAIL_RE, text)


def _tag(matched: str) -> str:
    return f"<{matched}>"


@pytest.mark.parametrize("text", _EDGE_CASES)
def test_replace_and_contains_match_re_subn(text: str) -> None:
    expected = _REFERENCE_EMAIL_RE.subn(lambda m: _tag(m.group(0)), text)
    assert replace_email_spans(text, _tag) == expected
    expected_enc = _REFERENCE_ENCODED_EMAIL_RE.subn(lambda m: _tag(m.group(0)), text)
    assert replace_encoded_email_spans(text, _tag) == expected_enc
    assert contains_email(text) == bool(_REFERENCE_EMAIL_RE.search(text))
    assert contains_encoded_email(text) == bool(_REFERENCE_ENCODED_EMAIL_RE.search(text))


# n is large enough that the quadratic regex takes seconds and a linear scan takes
# milliseconds, so the limits are not flaky on a slow runner.
_N = 20_000
_EMAIL_SCALE: Dict[str, Callable[[int], str]] = {
    "local-run": lambda n: "a" * n + "@b",
    "dotted-domain": lambda n: "a@" + "1." * (n // 2),
    "letter-domain": lambda n: "a@" + "a." * (n // 2),
    "escaped-quotes": lambda n: '"' + '\\"' * (n // 2),
    "quoted-body": lambda n: '"' + "a" * n + '"@bb.cc',
    "many-emails": lambda n: "a@bb.cc " * (n // 8),
    "at-signs": lambda n: "@" * n,
    "quotes": lambda n: '"' * n,
}
_ENCODED_SCALE: Dict[str, Callable[[int], str]] = {
    "local-run": lambda n: "a" * n + "%40",
    "dotted-domain": lambda n: "a%40" + "a." * (n // 2),
    "digit-domain": lambda n: "a%40" + "1." * (n // 2),
    "pct-run": lambda n: "%41" * (n // 3) + "%40",
    "encoded-dots": lambda n: "a%40" + "a%2E" * (n // 4) + "1",
    "many-emails": lambda n: "x%40y.zz " * (n // 9),
    "separators": lambda n: "%40" * (n // 3),
    # Digit labels joined by ``%2E`` and no valid TLD: exponential with the old pattern.
    "ambiguous-separator": lambda n: "a%40" + "1%2E" * (n // 4),
    "ambiguous-separator-tld": lambda n: "a%40" + "a%2E" * (n // 4) + "1",
}
_FINDERS: Dict[str, Callable[[str], Iterator[Tuple[int, int]]]] = {
    "email": find_email_spans,
    "encoded": find_encoded_email_spans,
}
_SCALE_PARAMS = [("email", shape) for shape in sorted(_EMAIL_SCALE)] + [
    ("encoded", shape) for shape in sorted(_ENCODED_SCALE)
]


def _scale_text(kind: str, shape: str) -> str:
    builders = _EMAIL_SCALE if kind == "email" else _ENCODED_SCALE
    return builders[shape](_N)


@pytest.mark.parametrize(("kind", "shape"), _SCALE_PARAMS)
def test_scanner_scales_linearly_by_shape(kind: str, shape: str) -> None:
    text = _scale_text(kind, shape)
    started = time.perf_counter()
    for _ in _FINDERS[kind](text):
        pass
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(("kind", "shape"), _SCALE_PARAMS)
def test_scrub_event_message_scales_linearly_by_shape(kind: str, shape: str) -> None:
    text = _scale_text(kind, shape)
    started = time.perf_counter()
    config.scrub_event({"message": text}, None)
    assert time.perf_counter() - started < 2.0
