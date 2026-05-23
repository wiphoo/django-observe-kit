"""Wagtail audit hooks must keep the raw tenant ID in structured logs even
after the cardinality cap collapses the metric label (codex P2 on PR #18).

The metric label needs cardinality bounding to keep Prometheus healthy; the
log field does not — logs are not aggregated into a label space and losing
tenant-level observability in logs exactly when high-cardinality traffic
occurs would be a regression.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory, override_settings

from observe_kit.context import RequestContext, reset_request_context, set_request_context
from observe_kit.metrics.prometheus import (
    OVERFLOW_LABEL,
    WAGTAIL_PUBLISHED,
    _reset_label_guards_for_tests,
)


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_request_context()
    _reset_label_guards_for_tests()
    yield
    reset_request_context()
    _reset_label_guards_for_tests()


@pytest.fixture
def wagtail_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("wagtail") is not None


@override_settings(OBSERVE_KIT={"METRICS_MAX_LABEL_CARDINALITY": 1})
def test_wagtail_publish_log_keeps_raw_tenant_when_metric_overflows(
    rf: RequestFactory, wagtail_available: bool
) -> None:
    """After the cardinality cap is exhausted, the metric label collapses to
    the overflow sentinel — but the log payload must keep the **real**
    tenant_id so operators don't lose tenant-level signal in logs."""
    if not wagtail_available:
        pytest.skip("wagtail not installed in this test env")

    from observe_kit.wagtail_integration.wagtail_hooks import audit_publish_page

    # First publish — fills the cap with "tenant-a".
    ctx = RequestContext()
    ctx.tenant_id = "tenant-a"
    set_request_context(ctx)
    request = rf.get("/admin/")
    page = Mock(id=1)
    with patch("observe_kit.wagtail_integration.wagtail_hooks.audit"):
        audit_publish_page(request, page)

    # Second publish — "tenant-b" must collapse to overflow on the metric...
    ctx2 = RequestContext()
    ctx2.tenant_id = "tenant-b"
    set_request_context(ctx2)
    page2 = Mock(id=2)

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    wagtail_logger = logging.getLogger("observe_kit.wagtail_integration.wagtail_hooks")
    prior_level = wagtail_logger.level
    wagtail_logger.setLevel(logging.DEBUG)
    wagtail_logger.addHandler(handler)
    try:
        with patch("observe_kit.wagtail_integration.wagtail_hooks.audit"):
            audit_publish_page(request, page2)
    finally:
        wagtail_logger.removeHandler(handler)
        wagtail_logger.setLevel(prior_level)

    # Metric: overflow series should have at least one observation now.
    assert WAGTAIL_PUBLISHED.labels(OVERFLOW_LABEL)._value.get() >= 1.0

    # ...but the log record must carry the **raw** tenant_id ("tenant-b"),
    # not the overflow sentinel.
    publish_records = [r for r in captured if r.getMessage() == "wagtail_publish"]
    assert publish_records, "expected a wagtail_publish log record"
    rec = publish_records[-1]
    assert rec.tenant_id == "tenant-b", f"log should carry raw tenant_id; got {rec.tenant_id!r}"
    assert rec.tenant_id != OVERFLOW_LABEL
