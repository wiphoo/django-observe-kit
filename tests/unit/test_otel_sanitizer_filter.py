"""Tests for the legacy ``OTelLogRecordSanitizer`` filter shim (#16).

The original pre-PR-#3 public type was a ``logging.Filter`` subclass with a
no-arg constructor and an ``addFilter`` use-case. PR #3 introduced
``_SafeOTelLogHandler`` (a handler requiring ``logger_provider`` at
construction) and aliased the old name to it, which silently broke any
downstream code that did ``handler.addFilter(OTelLogRecordSanitizer())``.

This test suite locks the back-compat contract: the public name must
still be a ``logging.Filter`` subclass with a no-arg constructor that
coerces non-OTEL-safe extras to ``repr()``.
"""

from __future__ import annotations

import logging

import pytest

from observe_kit.otel.config import OTelLogRecordSanitizer, _SafeOTelLogHandler


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=0, msg="m", args=None, exc_info=None
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_is_a_logging_filter_subclass() -> None:
    """The public alias must remain compatible with `handler.addFilter(...)`."""
    assert issubclass(OTelLogRecordSanitizer, logging.Filter)


def test_no_arg_constructor() -> None:
    """`addFilter(OTelLogRecordSanitizer())` was the documented usage shape."""
    flt = OTelLogRecordSanitizer()
    assert isinstance(flt, logging.Filter)


def test_distinct_from_safe_handler() -> None:
    """Regression: must not be aliased to the handler — that was the bug."""
    assert OTelLogRecordSanitizer is not _SafeOTelLogHandler
    assert not issubclass(OTelLogRecordSanitizer, logging.Handler)


def test_filter_returns_true_so_record_propagates() -> None:
    record = _make_record()
    assert OTelLogRecordSanitizer().filter(record) is True


def test_filter_leaves_otel_safe_extras_alone() -> None:
    record = _make_record(safe_int=42, safe_str="hello", safe_list=[1, 2, 3])
    OTelLogRecordSanitizer().filter(record)
    assert record.safe_int == 42
    assert record.safe_str == "hello"
    assert record.safe_list == [1, 2, 3]


def test_filter_coerces_unsupported_extras_to_repr() -> None:
    class Unsanitisable:
        def __repr__(self) -> str:
            return "<Unsanitisable>"

    obj = Unsanitisable()
    record = _make_record(weird=obj)
    OTelLogRecordSanitizer().filter(record)
    assert record.weird == "<Unsanitisable>"


def test_filter_skips_private_and_core_fields() -> None:
    """Core LogRecord fields (`msg`, `args`, etc.) must not be mutated."""
    record = _make_record()
    record._private = object()  # leading underscore — should be skipped
    original_msg = record.msg
    OTelLogRecordSanitizer().filter(record)
    assert record.msg == original_msg
    # `_private` value untouched (still an object, not a repr string)
    assert isinstance(record._private, object) and not isinstance(record._private, str)


def test_addFilter_pattern_works_end_to_end() -> None:
    """The exact downstream usage pattern that was broken — `addFilter(...)`
    on a real handler — must work without raising."""

    captured: list[logging.LogRecord] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _CapturingHandler()
    handler.addFilter(OTelLogRecordSanitizer())

    logger = logging.getLogger("test_otel_sanitizer_filter")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    class Unsanitisable:
        def __repr__(self) -> str:
            return "<Unsanitisable>"

    logger.info("hello", extra={"weird": Unsanitisable(), "fine": "ok"})

    assert len(captured) == 1
    rec = captured[0]
    assert rec.weird == "<Unsanitisable>"
    assert rec.fine == "ok"


def test_safe_handler_still_works() -> None:
    """Make sure restoring the filter didn't break the handler path."""
    assert issubclass(_SafeOTelLogHandler, logging.Handler)


@pytest.mark.parametrize(
    "value,expected_mutated",
    [
        (None, False),
        (True, False),
        (b"bytes", False),
        (42, False),
        (3.14, False),
        ("string", False),
        ([1, 2, 3], False),
        ({"k": "v"}, False),
        (object(), True),
        ({1: "non-str-key"}, True),
    ],
)
def test_filter_coercion_matrix(value: object, expected_mutated: bool) -> None:
    record = _make_record(payload=value)
    OTelLogRecordSanitizer().filter(record)
    if expected_mutated:
        assert isinstance(record.payload, str)
        assert record.payload == repr(value)
    else:
        assert record.payload == value
