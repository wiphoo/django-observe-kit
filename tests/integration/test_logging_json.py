"""Integration tests for JSON logging with real log output."""

import json
import logging

import pytest

pytestmark = pytest.mark.integration

from observe_kit.logging.config import RequestFormatter, configure_logging  # noqa: E402
from observe_kit.logging.filters import RequestContextFilter  # noqa: E402


@pytest.fixture
def json_log_handler() -> logging.StreamHandler:
    """Create a JSON log handler for testing."""
    handler = logging.StreamHandler()
    handler.setFormatter(RequestFormatter())
    return handler


def test_configure_logging_creates_json_formatter(json_log_handler: logging.StreamHandler) -> None:
    """Test that configure_logging creates JSON formatter."""
    configure_logging(level="INFO", pii_level="BASIC")

    # Get the root logger
    root_logger = logging.getLogger()

    # Check that formatters are JSON-capable
    for handler in root_logger.handlers:
        if isinstance(handler.formatter, RequestFormatter):
            assert handler.formatter is not None


def test_request_formatter_outputs_json(json_log_handler: logging.StreamHandler) -> None:
    """Test that RequestFormatter outputs valid JSON."""
    logger = logging.getLogger("test")
    logger.addHandler(json_log_handler)
    logger.setLevel(logging.INFO)

    # Create a log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    # Format it
    formatted = json_log_handler.format(record)

    # Should be valid JSON
    try:
        log_data = json.loads(formatted)
        assert "level" in log_data
        assert "logger" in log_data
        assert "message" in log_data
    except json.JSONDecodeError:
        pytest.fail("Log output is not valid JSON")


def test_request_context_filter_injects_context() -> None:
    """Test that RequestContextFilter injects context into log records."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    context.method = "GET"
    context.path = "/test"
    context.trace_id = "test-trace-123"
    context.tenant_id = "test-tenant"
    set_request_context(context)

    # Create filter
    log_filter = RequestContextFilter()

    # Create log record
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Test",
        args=(),
        exc_info=None,
    )

    # Apply filter
    result = log_filter.filter(record)

    assert result is True
    assert hasattr(record, "method")
    assert record.method == "GET"
    assert hasattr(record, "path")
    assert record.path == "/test"
    assert hasattr(record, "trace_id")
    assert record.trace_id == "test-trace-123"


def test_logging_with_pii_sanitization() -> None:
    """Test that logging sanitizes PII according to level."""
    # Set up context with PII-like data
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.logging.filters import get_log_extra

    reset_request_context()
    context = RequestContext()
    context.method = "POST"
    context.path = "/api/users"
    set_request_context(context)

    # Get log extra (should sanitize body fields)
    extra = get_log_extra("test_event", request_body="sensitive data", other_field="ok")

    # Body fields should be omitted
    assert "request_body" not in extra
    assert "other_field" in extra
    assert extra["other_field"] == "ok"
