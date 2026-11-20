"""Differential tests: the fused visit-once walk equals the multi-pass reference (#100)."""

from __future__ import annotations

import copy
from typing import Any, FrozenSet, Optional

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config

_KEYS = (
    st.sampled_from(
        [
            "phone",
            "email",
            "url",
            "message",
            "value",
            "params",
            "db.statement",
            "http.query",
            "Phone",
            "a@b.co",
            "b'phone'",
            "b'url'",
            "x",
            "token",
            "ip",
            "ip_address",
            "k",
        ]
    )
    | st.sampled_from([b"phone", b"x", b"url"])
    | st.integers(0, 2)
)
_LEAVES = (
    st.sampled_from(
        [
            "alice@example.com",
            "/s?phone=0812345678",
            "https://u:p@h/p?token=abc&x=1",
            "plain",
            "SELECT 1",
            "a%40b.com",
            "",
        ]
    )
    | st.sampled_from([b"alice@example.com", b"x"])
    | st.none()
    | st.integers()
    | st.booleans()
)


def _nodes() -> st.SearchStrategy[Any]:
    return st.recursive(
        _LEAVES,
        lambda c: st.one_of(
            st.dictionaries(_KEYS, c, max_size=4),
            st.lists(c, max_size=4),
            st.lists(st.tuples(_KEYS, c).map(list), min_size=1, max_size=3),
            st.lists(c, max_size=3).map(tuple),
            st.lists(st.sampled_from(["a@b.co", "x", 1]), max_size=3).map(frozenset),
        ),
        max_leaves=14,
    )


_SETS = st.none() | st.frozensets(
    st.sampled_from(["phone", "token", "k", "x", "url", "message", "ip", "email"]), max_size=3
)


@settings(max_examples=400, deadline=None, suppress_health_check=list(HealthCheck))
@given(
    node=st.one_of(st.dictionaries(_KEYS, _nodes(), max_size=5), st.lists(_nodes(), max_size=4)),
    level=st.sampled_from([PiiLevel.BASIC, PiiLevel.SENSITIVE]),
    drop=_SETS,
    mask=_SETS,
    hsh=_SETS,
)
def test_fused_walk_equals_multi_pass_reference(
    node: Any,
    level: PiiLevel,
    drop: Optional[FrozenSet[str]],
    mask: Optional[FrozenSet[str]],
    hsh: Optional[FrozenSet[str]],
) -> None:
    opts, _, content = config._build_opts(level, "salt", drop, mask, hsh)
    expected = config._fused_reference(copy.deepcopy(node), content, opts)
    assert config._fused_walk(copy.deepcopy(node), content, opts) == expected


def test_fused_walk_does_not_mutate_aliased_children_twice() -> None:
    shared = {"phone": "0812345678", "ip": "192.0.2.1"}
    opts, _, content = config._build_opts(PiiLevel.SENSITIVE, "salt", None, None, None)
    out = config._fused_walk({"a": shared, "b": shared}, content, opts)
    assert out["a"] == out["b"]
