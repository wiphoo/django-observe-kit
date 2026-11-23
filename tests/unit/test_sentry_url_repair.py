"""Repair layer that lets ``urlsplit`` parse URLs it would otherwise reject (#98)."""

from __future__ import annotations

import pytest

from observe_kit.sentry.config import _repair_url, _split_path, _split_repaired


@pytest.mark.parametrize("url", ["http://[", "http://[/p?x=1#f", "https://h]/a", "https://s[%2F"])
def test_unbalanced_brackets_are_repaired_and_restored(url: str) -> None:
    parts, repaired = _split_repaired(url)  # type: ignore[misc]
    assert repaired
    assert "" not in "".join(parts) and "" not in "".join(parts)
    assert parts.geturl() == url


def test_parseable_url_is_not_marked_repaired() -> None:
    parts, repaired = _split_repaired("http://[::1]:80/p?x=1")  # type: ignore[misc]
    assert not repaired and parts.netloc == "[::1]:80"


def test_input_containing_sentinel_is_unrepairable() -> None:
    assert _split_repaired("http://[") is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("//[redirect?a=1#f", ("//[redirect", "a=1", "f")),
        ("rootless/x?a=1", ("rootless/x", "a=1", "")),
        ("/a%2Fb#frag", ("/a%2Fb", "", "frag")),
        ("", ("", "", "")),
    ],
)
def test_split_path_uses_urlsplit_without_reading_authority(
    path: str, expected: tuple[str, str, str]
) -> None:
    assert _split_path(path) == expected


def test_repair_exposes_hidden_query_in_bracket_url() -> None:
    assert _repair_url("https://safe.test[%252Fsearch%253Fphone%253D0812345678") == (
        "https://safe.test[/search?phone=0812345678"
    )
