"""Golden-snapshot and invariant tests for the Sentry URL locator seam (#99).

The golden file was recorded from the pre-refactor scrubber. It must never be
regenerated to make a failing test pass: a diff means the locator changed
behavior, so fix the locator.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, List, Literal, Pattern, Tuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config
from observe_kit.sentry.scrub.locate import UrlSpan, find_url_spans, split_url_token

GOLDEN_PATH = Path(__file__).parent / "data" / "url_locate_golden.json"

CORPUS: List[str] = [
    "",
    "no url here, just prose; and/or maybe?",
    "plain alice@example.com mail",
    "/just/a/path",
    "?phone=0812345678",
    "a?b=c#d=e",
    "see #section and/or?maybe",
    "connect https://user:secret@host/db now",
    "postgres://user:secret@host/db",
    "wss://u:p@h.test/ws?key=abc",
    "HTTPS://Example.com/p?email=a@b.co",
    "//user:secret@host/path",
    "url=https://x.test/p?token=abc&y=2",
    "https://[::1]:8000/p?token=abc",
    "(https://a.test/p?phone=0812345678)",
    "'https://a.test/p?phone=0812345678'",
    "GET /search?phone=0812345678 HTTP/1.1",
    "/callback#access_token=abc123",
    "redirect callback?phone=0812345678",
    "account/callback?phone=0812345678",
    "https://safe.test,postgres://user:secret@host/db",
    "https://a.test/x;https://user:pw@b.test/y|ftp://u:p@c.test/",
    "https://safe.test?x=a,b&phone=0812345678",
    "https://safe.test,progress=100%25",
    "https://safe.test then %252Fsearch%253Fphone%253D0812345678",
    "redirect:%2Fsearch%3Fphone%3D0812345678,hello%20world",
    "https://h/p?next=%2Fa%3Ftoken%3Dabc",
    "//h/p?next=%2Fa%3Ftoken%3Dabc",
    "https://user%253Apass%2540host/x",
    "mixed https://a.test/p?x=1 and /b?phone=0812345678 and cb?token=zzz",
    "/a?x=1//h/p?token=abc",
]

LEVELS = (("basic", PiiLevel.BASIC, ""), ("sensitive", PiiLevel.SENSITIVE, "salt"))


def scrub_case(level: PiiLevel, salt: str, text: str) -> Dict[str, str]:
    opts = config._build_opts(level, salt, None, None, None)[0]
    return {
        "text": config._scrub_text(text, opts, 0),
        "urlish": config._scrub_urlish_value(text, opts, 0),
    }


def build_golden() -> Dict[str, Dict[str, str]]:
    return {
        f"{name}|{text}": scrub_case(level, salt, text)
        for name, level, salt in LEVELS
        for text in CORPUS
    }


@pytest.mark.parametrize(("name", "level", "salt"), LEVELS)
@pytest.mark.parametrize("text", CORPUS)
def test_scrub_output_matches_golden(name: str, level: PiiLevel, salt: str, text: str) -> None:
    golden = json.loads(GOLDEN_PATH.read_text())
    assert scrub_case(level, salt, text) == golden[f"{name}|{text}"]


_HYP = settings(deadline=timedelta(milliseconds=1000), max_examples=50)
_ALPHABET = "abc:/?#=&@.,;|)]}%25F3 \"'<>-_~+[]"


def test_find_url_spans_kinds() -> None:
    text = "see https://a.test/p?x=1 and /b?phone=1 and cb?token=z"
    assert [(s.kind, text[s.start : s.end]) for s in find_url_spans(text)] == [
        ("absolute", "https://a.test/p?x=1"),
        ("relative", "/b?phone=1"),
        ("rootless", "cb?token=z"),
    ]


def test_find_url_spans_kinds_filter() -> None:
    text = "https://a.test/p?x=1 /b?phone=1"
    assert [s.kind for s in find_url_spans(text, kinds=("absolute",))] == ["absolute"]


def test_split_url_token_splits_at_adjacent_url_start_without_separator() -> None:
    assert split_url_token("https://a.test/x//u:p@h/y") == [
        ("https://a.test/x", True),
        ("//u:p@h/y", True),
    ]


def test_split_url_token_splits_adjacent_urls() -> None:
    assert split_url_token("https://safe.test,postgres://u:p@h/db") == [
        ("https://safe.test", True),
        (",", True),
        ("postgres://u:p@h/db", True),
    ]


@_HYP
@given(text=st.text(alphabet=_ALPHABET, max_size=60))
def test_spans_are_ordered_disjoint_and_in_bounds(text: str) -> None:
    spans = find_url_spans(text)
    previous_end = 0
    for span in spans:
        assert previous_end <= span.start < span.end <= len(text)
        previous_end = span.end


@_HYP
@given(text=st.text(alphabet=_ALPHABET, max_size=60))
def test_split_url_token_round_trips(text: str) -> None:
    assert "".join(piece for piece, _ in split_url_token(text)) == text


# --- Reference oracles (#193) -------------------------------------------------
# Verbatim copies of the regexes locate.py used before the linear scanners. They
# are the specification: find_url_spans / split_url_token must return exactly the
# spans these produce. Do not "improve" them.
Kind = Literal["absolute", "relative", "rootless"]
_REFERENCE_PATTERNS: Tuple[Tuple[Kind, Pattern[str]], ...] = (
    (
        "absolute",
        re.compile(
            r"(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^\s,;|)\]}<>\"']+"
            r"(?:[,;)][^\s&,;|)\]}<>\"']*&[^\s,;|)\]}<>\"']*=[^\s,;|)\]}<>\"']*)*",
            re.IGNORECASE,
        ),
    ),
    (
        "relative",
        re.compile(
            r"(?<![\w:/])/[^\s?#\"'<>]*[?#]"
            r"[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*"
            r"(?:[,;][^\s&,;|)\]}<>\"']+&[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*)*"
        ),
    ),
    ("rootless", re.compile(r"(?<![\w/])[\w@.+~-][\w@.+/~-]*[?#][\w.%\-]+=[^\s,;|)\]}<>\"']*")),
)
_REF_SEPARATOR_RE = re.compile(r"([,;|)\]}])(?=(?:[A-Za-z][A-Za-z0-9+.-]*:)?//)")
_REF_NEXT_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|//(?=[^/?#\s]*@)")


def _reference_split_url_token(token: str) -> List[Tuple[str, bool]]:
    pieces: List[Tuple[str, bool]] = []
    for part in _REF_SEPARATOR_RE.split(token):
        if not part:
            pieces.append((part, False))
            continue
        start = 0
        for match in _REF_NEXT_URL_RE.finditer(part):
            if match.start() == 0:
                continue
            pieces.append((part[start : match.start()], True))
            start = match.start()
        pieces.append((part[start:], True))
    return pieces


def _naive_find_url_spans(text: str, kinds: Tuple[Kind, ...]) -> List[UrlSpan]:
    spans: List[UrlSpan] = []
    for kind, pattern in _REFERENCE_PATTERNS:
        if kind not in kinds:
            continue
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < other.end and other.start < end for other in spans):
                continue
            spans.append(UrlSpan(start, end, kind))
    spans.sort()
    return spans


@_HYP
@given(text=st.text(alphabet=_ALPHABET, max_size=60))
def test_find_url_spans_matches_naive_reference(text: str) -> None:
    for kinds in (("absolute", "relative", "rootless"), ("absolute", "relative")):
        assert find_url_spans(text, kinds=kinds) == _naive_find_url_spans(text, kinds)


def test_find_url_spans_scales_linearly() -> None:
    text = "see https://a.test/p?x=1 and /b?phone=1 and cb?token=z " * 6000
    started = time.perf_counter()
    spans = find_url_spans(text)
    elapsed = time.perf_counter() - started
    assert len(spans) == 18000
    assert elapsed < 3.0


# Tokens chosen to hit every anchor and boundary the scanners rely on, including
# U+017F and U+212A, the two non-ASCII letters that case-fold into [A-Za-z] under
# re.IGNORECASE (so they matter for the absolute pattern but not for the next-URL one).
_SCAN_TOKENS = [
    "a", "Z", "1", ":", "/", "?", "#", "=", "&", ",", ";", ")", "@", ".", "-", "+", "~",
    "%", " ", "\n", '"', "'", "<", ">", "//", "://", "http://", "\u017f", "\u212a", "\u00e9",
    "a=b", "?k=v", "/p?k=v", "/a?", "&x=y", ",a&b=c", ";a&b=c", "#k=v", "x/y?a=b", "a.b?k=v",
    "-a?k=v", "/a?k=v",
]  # fmt: skip
_SCAN_TEXT = st.lists(st.sampled_from(_SCAN_TOKENS), max_size=14).map("".join)
_DIFF = settings(max_examples=3000, deadline=None, derandomize=True)


@_DIFF
@given(text=_SCAN_TEXT)
def test_single_kind_spans_match_reference_regex(text: str) -> None:
    for kind, pattern in _REFERENCE_PATTERNS:
        expected = [UrlSpan(*match.span(), kind) for match in pattern.finditer(text)]
        assert find_url_spans(text, kinds=(kind,)) == expected, (kind, text)


@_DIFF
@given(text=_SCAN_TEXT)
def test_split_url_token_matches_reference(text: str) -> None:
    assert split_url_token(text) == _reference_split_url_token(text), text


_KIND_COMBOS: Tuple[Tuple[Kind, ...], ...] = (
    ("absolute", "relative", "rootless"),
    ("absolute", "relative"),
)


@_DIFF
@given(text=_SCAN_TEXT)
def test_merged_spans_match_naive_overlap_resolution(text: str) -> None:
    for kinds in _KIND_COMBOS:
        assert find_url_spans(text, kinds=kinds) == _naive_find_url_spans(text, kinds), text


_EDGE_CASES = [
    "xhttps://h/p?a=b",
    "1abc://h/p",
    "1://h",
    "\u017fhttps://h/p",
    "\u212ahttp://h/p",
    "a://",
    "://x",
    "http:///x",
    "///",
    "// ",
    "//",
    "http://a,http://b?x=1&y=2",
    "ab+c-d.e://h/p?k=v",
    "see //u:p@h/db and https://x.test/p",
    "x//u@h",
    "a.b.c?k=v",
    "a@b?k=v",
    "-a?k=v",
    "a/b/c?k=v",
    "//a/b?k=v",
    "GET /x?a=b,c&d=e;f&g=h",
    "/a?/b?/c?k=v",
    "/a?x /b?k=v",
    "/?k=v",
    "/#k=v",
    "x/?k=v",
    ":/a?k=v",
]


@pytest.mark.parametrize("text", _EDGE_CASES)
def test_edge_cases_match_reference(text: str) -> None:
    for kind, pattern in _REFERENCE_PATTERNS:
        expected = [UrlSpan(*match.span(), kind) for match in pattern.finditer(text)]
        assert find_url_spans(text, kinds=(kind,)) == expected, (kind, text)
    assert split_url_token(text) == _reference_split_url_token(text)
    for kinds in _KIND_COMBOS:
        assert find_url_spans(text, kinds=kinds) == _naive_find_url_spans(text, kinds), text


# (builder, n): n is large enough that the quadratic version takes seconds and the
# linear one takes milliseconds, so the limits (1 s for find/split, 2 s for
# scrub_event) are not flaky on a slow runner.
_SCALE_CASES: Dict[str, Tuple[Callable[[int], str], int]] = {
    "letters": (lambda n: "a" * n, 30_000),
    "letters-query": (lambda n: "a" * n + "?k=v", 30_000),
    "slash-letters-query": (lambda n: "/" + "a" * n + "?", 30_000),
    "dotted": (lambda n: "a." * (n // 2), 30_000),
    "dashes": (lambda n: "a-" * (n // 2), 30_000),
    "scheme-path": (lambda n: "https://h/" + "a" * n, 100_000),
    "relative-run": (lambda n: "/a?" * (n // 3), 100_000),
    "digits": (lambda n: "1" * n, 30_000),
    "colons": (lambda n: "a:" * (n // 2), 30_000),
    "slashes": (lambda n: "/" * n, 30_000),
}


def _scale_text(shape: str) -> str:
    build, n = _SCALE_CASES[shape]
    return build(n)


@pytest.mark.parametrize("shape", sorted(_SCALE_CASES))
def test_find_url_spans_scales_linearly_by_shape(shape: str) -> None:
    text = _scale_text(shape)
    started = time.perf_counter()
    find_url_spans(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("shape", sorted(_SCALE_CASES))
def test_split_url_token_scales_linearly_by_shape(shape: str) -> None:
    text = _scale_text(shape)
    started = time.perf_counter()
    split_url_token(text)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("shape", sorted(_SCALE_CASES))
def test_scrub_event_message_scales_linearly_by_shape(shape: str) -> None:
    text = _scale_text(shape)
    started = time.perf_counter()
    config.scrub_event({"message": text}, None)
    assert time.perf_counter() - started < 2.0


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(build_golden(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {GOLDEN_PATH}")
