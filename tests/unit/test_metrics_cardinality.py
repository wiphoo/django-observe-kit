"""Tests for the Prometheus label cardinality cap and route fallback fix (#9)."""

from __future__ import annotations

import pytest
from django.test import override_settings

from observe_kit.metrics.prometheus import (
    HTTP_REQUESTS_TOTAL,
    OVERFLOW_LABEL,
    _BoundedLabelSet,
    _reset_label_guards_for_tests,
    observe_request,
)


@pytest.fixture(autouse=True)
def _reset_guards() -> None:
    _reset_label_guards_for_tests()
    yield
    _reset_label_guards_for_tests()


# ---------------------------------------------------------------------------
# _BoundedLabelSet — pure helper
# ---------------------------------------------------------------------------


def test_bounded_set_admits_up_to_cap() -> None:
    guard = _BoundedLabelSet(max_size=3)
    assert guard.admit("a") == "a"
    assert guard.admit("b") == "b"
    assert guard.admit("c") == "c"


def test_bounded_set_collapses_beyond_cap() -> None:
    guard = _BoundedLabelSet(max_size=2)
    guard.admit("a")
    guard.admit("b")
    assert guard.admit("c") == OVERFLOW_LABEL
    assert guard.admit("d") == OVERFLOW_LABEL


def test_bounded_set_known_values_pass_after_cap_reached() -> None:
    guard = _BoundedLabelSet(max_size=2)
    guard.admit("a")
    guard.admit("b")
    guard.admit("c")  # → overflow
    # "a" and "b" still pass through.
    assert guard.admit("a") == "a"
    assert guard.admit("b") == "b"


def test_bounded_set_cap_zero_disables() -> None:
    guard = _BoundedLabelSet(max_size=0)
    for i in range(50):
        assert guard.admit(f"v{i}") == f"v{i}"


def test_bounded_set_reset_clears_seen() -> None:
    guard = _BoundedLabelSet(max_size=1)
    guard.admit("a")
    assert guard.admit("b") == OVERFLOW_LABEL
    guard.reset()
    # After reset, "b" can be admitted again as the first entry.
    assert guard.admit("b") == "b"


# ---------------------------------------------------------------------------
# Sentinel collision protection (qodo bug #2)
# ---------------------------------------------------------------------------


def test_sentinel_label_is_reserved() -> None:
    """An attacker submitting the literal sentinel must never enter the seen
    set — otherwise they could forge or pollute the overflow bucket."""
    from observe_kit.metrics.prometheus import OVERFLOW_LABEL as sentinel

    # Sentinel is not a plausible legitimate value.
    assert sentinel.startswith("__") and sentinel.endswith("__")
    assert "overflow" in sentinel
    # Forging it directly collapses to overflow regardless of cap.
    guard = _BoundedLabelSet(max_size=100)
    assert guard.admit(sentinel) == sentinel
    # It does NOT consume a slot in the seen set, so legitimate labels are not displaced.
    for i in range(10):
        assert guard.admit(f"legit-{i}") == f"legit-{i}"


def test_sentinel_label_blocked_even_when_cap_disabled() -> None:
    guard = _BoundedLabelSet(max_size=0)
    from observe_kit.metrics.prometheus import OVERFLOW_LABEL as sentinel

    assert guard.admit(sentinel) == sentinel
    assert guard.admit("legit") == "legit"


# ---------------------------------------------------------------------------
# Lock-skip optimisation after cap is reached (qodo bug #4)
# ---------------------------------------------------------------------------


def test_full_flag_short_circuits_lock_after_cap() -> None:
    """After the cap is reached, unseen values must NOT acquire the lock."""
    guard = _BoundedLabelSet(max_size=2)
    guard.admit("a")
    guard.admit("b")
    # _full flag must be set now that we've reached the cap.
    assert guard._full is True

    # Patch the lock to fail if used — prove the fast-path doesn't acquire it.
    class _ExplodingLock:
        def __enter__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("lock should not be acquired after cap is reached")

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            return False

    guard._lock = _ExplodingLock()

    # New unseen values must return overflow without touching the lock.
    assert guard.admit("c") == OVERFLOW_LABEL
    assert guard.admit("d") == OVERFLOW_LABEL
    # Already-known values also bypass the lock.
    assert guard.admit("a") == "a"


def test_full_flag_cleared_by_reset() -> None:
    guard = _BoundedLabelSet(max_size=1)
    guard.admit("a")
    assert guard.admit("b") == OVERFLOW_LABEL
    assert guard._full is True
    guard.reset()
    assert guard._full is False
    # Now "b" can be admitted as the first entry again.
    assert guard.admit("b") == "b"


# ---------------------------------------------------------------------------
# observe_request — route guard kicks in
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 3})
def test_observe_request_route_collapses_beyond_cap() -> None:
    # Three distinct routes admitted; fourth lands on overflow.
    for i in range(3):
        observe_request(
            method="GET",
            route=f"/route-{i}",
            status=200,
            duration_seconds=0.01,
            tenant="t1",
            db_queries=1,
            db_time_seconds=0.001,
        )
    observe_request(
        method="GET",
        route="/route-overflow-candidate",
        status=200,
        duration_seconds=0.01,
        tenant="t1",
        db_queries=1,
        db_time_seconds=0.001,
    )

    # Verify the overflow series exists on HTTP_REQUESTS_TOTAL.
    overflow_value = HTTP_REQUESTS_TOTAL.labels("GET", OVERFLOW_LABEL, "200", "t1")._value.get()
    assert overflow_value >= 1.0


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 2})
def test_observe_request_tenant_collapses_independently() -> None:
    # Two tenants admitted; a third tenant on a known route still collapses to overflow.
    for tenant in ("tenant-a", "tenant-b"):
        observe_request(
            method="GET",
            route="/shared",
            status=200,
            duration_seconds=0.01,
            tenant=tenant,
            db_queries=1,
            db_time_seconds=0.001,
        )
    observe_request(
        method="GET",
        route="/shared",
        status=200,
        duration_seconds=0.01,
        tenant="tenant-c",
        db_queries=1,
        db_time_seconds=0.001,
    )

    overflow_tenant = HTTP_REQUESTS_TOTAL.labels(
        "GET", "/shared", "200", OVERFLOW_LABEL
    )._value.get()
    assert overflow_tenant >= 1.0


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 0})
def test_observe_request_cap_zero_disables_guard() -> None:
    # With cap disabled, many distinct routes all pass through verbatim.
    for i in range(10):
        observe_request(
            method="GET",
            route=f"/uncapped-{i}",
            status=200,
            duration_seconds=0.01,
            tenant="t1",
            db_queries=1,
            db_time_seconds=0.001,
        )
    # The 10th distinct route should still be its own series, not overflow.
    value = HTTP_REQUESTS_TOTAL.labels("GET", "/uncapped-9", "200", "t1")._value.get()
    assert value >= 1.0


# ---------------------------------------------------------------------------
# settings parsing
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 250})
def test_settings_parse_cap() -> None:
    from observe_kit.settings import get_observe_kit_settings

    assert get_observe_kit_settings().metrics_max_label_cardinality == 250


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": -5})
def test_settings_clamp_negative_to_zero() -> None:
    from observe_kit.settings import get_observe_kit_settings

    assert get_observe_kit_settings().metrics_max_label_cardinality == 0


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": "garbage"})
def test_settings_invalid_falls_back_to_default() -> None:
    from observe_kit.settings import get_observe_kit_settings

    assert get_observe_kit_settings().metrics_max_label_cardinality == 1000


@override_settings(OBSERVE_KIT={})
def test_settings_default_is_1000() -> None:
    from observe_kit.settings import get_observe_kit_settings

    assert get_observe_kit_settings().metrics_max_label_cardinality == 1000
