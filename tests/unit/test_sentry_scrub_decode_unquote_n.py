"""``unquote_n`` decodes by a fixed depth, stops when stable, and honours the cap."""

from __future__ import annotations

import pytest

from observe_kit.sentry.scrub.decode import MAX_DECODE_PASSES, unquote_n


@pytest.mark.parametrize(
    ("value", "levels", "expected"),
    [
        ("a%2526b", 1, "a%26b"),
        ("a%2526b", 2, "a&b"),
        ("a%2526b", 5, "a&b"),  # stable early: extra levels are no-ops
        ("a%2526b", 0, "a%2526b"),
    ],
)
def test_unquote_n_fixed_depth(value: str, levels: int, expected: str) -> None:
    assert unquote_n(value, levels) == expected


def test_unquote_n_is_capped() -> None:
    deep = "%" + "25" * 10 + "41"  # many layers of 'A'
    assert unquote_n(deep, 100) == unquote_n(deep, MAX_DECODE_PASSES)
