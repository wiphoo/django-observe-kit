"""Tests for ``guard_tenant_label`` and its application in audit + Wagtail
metrics call sites (#13)."""

from __future__ import annotations

import pytest
from django.test import override_settings

from observe_kit.metrics.prometheus import (
    AUDIT_EVENTS,
    OVERFLOW_LABEL,
    WAGTAIL_DELETED,
    WAGTAIL_PUBLISHED,
    WAGTAIL_UNPUBLISHED,
    _reset_label_guards_for_tests,
    guard_tenant_label,
)


@pytest.fixture(autouse=True)
def _reset_guards() -> None:
    _reset_label_guards_for_tests()
    yield
    _reset_label_guards_for_tests()


# ---------------------------------------------------------------------------
# Pure helper behaviour
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 3})
def test_guard_admits_under_cap() -> None:
    assert guard_tenant_label("a") == "a"
    assert guard_tenant_label("b") == "b"
    assert guard_tenant_label("c") == "c"


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 2})
def test_guard_collapses_over_cap() -> None:
    guard_tenant_label("a")
    guard_tenant_label("b")
    assert guard_tenant_label("c") == OVERFLOW_LABEL
    assert guard_tenant_label("d") == OVERFLOW_LABEL


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 100})
def test_guard_maps_none_and_empty_to_unknown() -> None:
    assert guard_tenant_label(None) == "unknown"
    assert guard_tenant_label("") == "unknown"


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 100})
def test_guard_rejects_literal_sentinel() -> None:
    # Attacker submits the sentinel itself — must never enter the seen set,
    # so legitimate values are not displaced.
    assert guard_tenant_label(OVERFLOW_LABEL) == OVERFLOW_LABEL
    for i in range(10):
        assert guard_tenant_label(f"legit-{i}") == f"legit-{i}"


# ---------------------------------------------------------------------------
# AUDIT_EVENTS now routed through the guard (audit/utils.audit())
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 2})
def test_audit_events_collapse_to_overflow_beyond_cap() -> None:
    from observe_kit.audit.utils import audit

    for tenant in ("a", "b"):
        # Stub the FK + DB write — we only care about the metric label here.
        from observe_kit.context import RequestContext, set_request_context

        ctx = RequestContext()
        ctx.tenant_id = tenant
        set_request_context(ctx)
        audit(action=f"act-{tenant}")

    # Third tenant -> overflow
    from observe_kit.context import RequestContext, set_request_context

    ctx = RequestContext()
    ctx.tenant_id = "c"
    set_request_context(ctx)
    audit(action="act-c")

    overflow = AUDIT_EVENTS.labels(tenant=OVERFLOW_LABEL)._value.get()
    assert overflow >= 1.0


# ---------------------------------------------------------------------------
# Wagtail counters routed through the guard
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 1})
def test_wagtail_published_collapses_to_overflow_beyond_cap() -> None:
    # First tenant fills the cap.
    WAGTAIL_PUBLISHED.labels(guard_tenant_label("tenant-a")).inc()
    # Second tenant must hit overflow.
    WAGTAIL_PUBLISHED.labels(guard_tenant_label("tenant-b")).inc()
    overflow = WAGTAIL_PUBLISHED.labels(OVERFLOW_LABEL)._value.get()
    assert overflow >= 1.0


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 1})
def test_wagtail_unpublished_and_deleted_share_the_guard_state() -> None:
    """All Wagtail counters use the same `tenant_guard` instance, so once the
    cap is reached for any of them the rest also fold to overflow."""
    WAGTAIL_PUBLISHED.labels(guard_tenant_label("only-tenant")).inc()
    # Now everything else lands on overflow.
    WAGTAIL_UNPUBLISHED.labels(guard_tenant_label("new-tenant")).inc()
    WAGTAIL_DELETED.labels(guard_tenant_label("yet-another")).inc()
    assert WAGTAIL_UNPUBLISHED.labels(OVERFLOW_LABEL)._value.get() >= 1.0
    assert WAGTAIL_DELETED.labels(OVERFLOW_LABEL)._value.get() >= 1.0
