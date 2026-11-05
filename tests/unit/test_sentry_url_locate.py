"""Golden-snapshot and invariant tests for the Sentry URL locator seam (#99).

The golden file was recorded from the pre-refactor scrubber. It must never be
regenerated to make a failing test pass: a diff means the locator changed
behavior, so fix the locator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config

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


if __name__ == "__main__":
    if "--write" in sys.argv:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(build_golden(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {GOLDEN_PATH}")
