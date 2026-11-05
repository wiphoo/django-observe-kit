"""Golden-snapshot and invariant tests for the Sentry URL locator seam (#99).

The golden file was recorded from the pre-refactor scrubber. It must never be
regenerated to make a failing test pass: a diff means the locator changed
behavior, so fix the locator.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config
from observe_kit.sentry.scrub.locate import _KIND_PATTERNS, UrlSpan, find_url_spans, split_url_token

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


def _naive_find_url_spans(text: str, kinds: Tuple[str, ...]) -> List[UrlSpan]:
    spans: List[UrlSpan] = []
    for kind, pattern in _KIND_PATTERNS:
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


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(build_golden(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {GOLDEN_PATH}")
