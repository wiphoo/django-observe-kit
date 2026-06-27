"""Edge case tests for logging filters."""

import logging

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_request_context_filter_applies_context() -> None:
    """Test that RequestContextFilter applies context to log record."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.logging.filters import RequestContextFilter

    reset_request_context()
    context = RequestContext()
    context.method = "GET"
    context.path = "/test"
    context.trace_id = "trace-123"
    set_request_context(context)

    filter_instance = RequestContextFilter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0, msg="test", args=(), exc_info=None
    )

    result = filter_instance.filter(record)

    assert result is True
    assert record.method == "GET"
    assert record.path == "/test"
    assert record.trace_id == "trace-123"


def test_request_context_filter_with_empty_context() -> None:
    """Test RequestContextFilter with empty context."""
    from observe_kit.context import reset_request_context
    from observe_kit.logging.filters import RequestContextFilter

    reset_request_context()

    filter_instance = RequestContextFilter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0, msg="test", args=(), exc_info=None
    )

    result = filter_instance.filter(record)

    assert result is True
    # Should not raise error even with empty context
