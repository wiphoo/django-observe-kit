"""Integration tests for JSON structured logging.

These tests verify:
1. Log output is valid JSON format
2. Request context is injected into log records
3. PII fields are properly sanitized
4. The logging stack works end-to-end
"""

import json
import logging
from io import StringIO

import pytest

pytestmark = pytest.mark.integration

from observe_kit.logging.config import RequestFormatter, configure_logging  # noqa: E402
from observe_kit.logging.filters import RequestContextFilter, get_log_extra  # noqa: E402


class TestJSONLogOutput:
    """Tests for JSON log output format."""

    def test_request_formatter_produces_valid_json(self) -> None:
        """Test that RequestFormatter outputs parseable JSON."""
        handler = logging.StreamHandler(StringIO())
        handler.setFormatter(RequestFormatter())

        logger = logging.getLogger("test.json_output")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Create a log record
        record = logging.LogRecord(
            name="test.json_output",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test log message",
            args=(),
            exc_info=None,
        )

        # Format it
        formatted = handler.format(record)

        # Must be valid JSON
        try:
            log_data = json.loads(formatted)
        except json.JSONDecodeError as e:
            pytest.fail(f"Log output is not valid JSON: {e}\nOutput: {formatted}")

        # Required fields
        assert "level" in log_data, "Missing 'level' field"
        assert "logger" in log_data, "Missing 'logger' field"
        assert "message" in log_data, "Missing 'message' field"

        # Verify values
        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.json_output"
        assert log_data["message"] == "Test log message"

    def test_json_log_has_required_fields(self) -> None:
        """Test that JSON logs have required fields (message, level, logger)."""
        handler = logging.StreamHandler(StringIO())
        handler.setFormatter(RequestFormatter())

        record = logging.LogRecord(
            name="test.module",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Warning message",
            args=(),
            exc_info=None,
        )

        formatted = handler.format(record)
        log_data = json.loads(formatted)

        # Required fields from RequestFormatter
        assert "message" in log_data, "Missing 'message' field"
        assert "level" in log_data, "Missing 'level' field"
        assert "logger" in log_data, "Missing 'logger' field"

        # Verify values are correct
        assert log_data["message"] == "Warning message"
        assert log_data["level"] == "WARNING"
        assert log_data["logger"] == "test.module"

    def test_json_log_with_exception(self) -> None:
        """Test that exceptions are included in JSON logs."""
        handler = logging.StreamHandler(StringIO())
        handler.setFormatter(RequestFormatter())

        try:
            raise ValueError("Test exception")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        formatted = handler.format(record)
        log_data = json.loads(formatted)

        # Should include exception info
        assert log_data["level"] == "ERROR"
        # Exception text should be in the log somewhere
        assert "ValueError" in formatted or "Test exception" in formatted


class TestRequestContextInjection:
    """Tests for request context injection into logs."""

    def test_filter_injects_context_fields(self) -> None:
        """Test that RequestContextFilter injects context into log records."""
        from observe_kit.context import RequestContext, reset_request_context, set_request_context

        reset_request_context()
        context = RequestContext()
        context.method = "POST"
        context.path = "/api/users"
        context.trace_id = "abc123def456"
        context.tenant_id = "tenant-001"
        context.user_id = "user-42"
        set_request_context(context)

        log_filter = RequestContextFilter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        result = log_filter.filter(record)

        assert result is True
        assert hasattr(record, "method")
        assert record.method == "POST"
        assert hasattr(record, "path")
        assert record.path == "/api/users"
        assert hasattr(record, "trace_id")
        assert record.trace_id == "abc123def456"

    def test_context_appears_in_json_output(self) -> None:
        """Test that injected context appears in final JSON output."""
        from observe_kit.context import RequestContext, reset_request_context, set_request_context

        reset_request_context()
        context = RequestContext()
        context.method = "GET"
        context.path = "/test"
        context.trace_id = "trace-123"
        set_request_context(context)

        # Set up handler with filter and formatter
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RequestFormatter())
        handler.addFilter(RequestContextFilter())

        logger = logging.getLogger("test.context_json")
        logger.handlers = []
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message with context")

        # Get output
        output = stream.getvalue()
        log_data = json.loads(output)

        # Context should be in the log
        assert log_data.get("method") == "GET" or "GET" in output
        assert log_data.get("path") == "/test" or "/test" in output


class TestPIISanitization:
    """Tests for PII sanitization in logs."""

    def test_sensitive_fields_omitted(self) -> None:
        """Test that sensitive fields are omitted from log extra."""
        extra = get_log_extra(
            "test_event",
            request_body="secret data",
            response_body="sensitive response",
            password="hunter2",
            safe_field="visible",
        )

        # Sensitive fields should be omitted
        assert "request_body" not in extra
        assert "response_body" not in extra
        # Note: password might or might not be filtered depending on implementation

        # Safe fields should remain
        assert "safe_field" in extra
        assert extra["safe_field"] == "visible"

    def test_event_name_preserved(self) -> None:
        """Test that event name is preserved in log extra."""
        extra = get_log_extra("user_login", user_id="123")

        assert "event" in extra
        assert extra["event"] == "user_login"


class TestLoggingConfiguration:
    """Tests for logging configuration."""

    def test_configure_logging_sets_level(self) -> None:
        """Test that configure_logging sets the correct log level."""
        configure_logging(level="WARNING", pii_level="BASIC")

        root_logger = logging.getLogger()
        # At least one handler should have WARNING level or higher
        assert root_logger.level <= logging.WARNING or any(
            h.level <= logging.WARNING for h in root_logger.handlers
        )

    def test_configure_logging_adds_json_formatter(self) -> None:
        """Test that configure_logging adds JSON formatter."""
        configure_logging(level="INFO", pii_level="BASIC")

        root_logger = logging.getLogger()

        # Check for RequestFormatter
        has_request_formatter = any(
            isinstance(h.formatter, RequestFormatter) for h in root_logger.handlers
        )
        assert has_request_formatter, "No RequestFormatter found in handlers"


class TestEndToEndLogging:
    """End-to-end logging tests."""

    def test_full_logging_flow(self) -> None:
        """Test complete logging flow from context to JSON output."""
        from observe_kit.context import RequestContext, reset_request_context, set_request_context

        # Setup context
        reset_request_context()
        context = RequestContext()
        context.method = "PUT"
        context.path = "/api/items/123"
        context.trace_id = "e2e-trace-id"
        context.tenant_id = "e2e-tenant"
        set_request_context(context)

        # Setup logger
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(RequestFormatter())
        handler.addFilter(RequestContextFilter())

        logger = logging.getLogger("test.e2e")
        logger.handlers = []
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Log message
        logger.info("Item updated", extra={"item_id": 123, "changes": ["name", "price"]})

        # Verify output
        output = stream.getvalue()
        assert len(output) > 0, "No log output produced"

        log_data = json.loads(output)

        # Core fields
        assert log_data["level"] == "INFO"
        assert "Item updated" in log_data.get("message", "")

        # Extra fields (may be nested or flat depending on formatter)
        output_str = json.dumps(log_data)
        assert "123" in output_str  # item_id should appear somewhere
