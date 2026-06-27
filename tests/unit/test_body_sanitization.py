"""Tests for request body sanitization."""

from observe_kit.conf import BODY_LOG_WARNING
from observe_kit.logging.filters import get_log_extra, sanitize_log_data


def test_get_log_extra_filters_body() -> None:
    """Test that get_log_extra filters out body fields."""
    from observe_kit.context import reset_request_context

    reset_request_context()

    extra = get_log_extra("test_event", request_body="sensitive data", other_field="ok")

    # Body fields should be omitted
    assert "request_body" not in extra
    assert "other_field" in extra
    assert extra["other_field"] == "ok"


def test_sanitize_log_data() -> None:
    """Test sanitize_log_data function."""
    data = {
        "body": "sensitive",
        "request_body": "sensitive",
        "response_body": "sensitive",
        "data": "sensitive",
        "payload": "sensitive",
        "content": "sensitive",
        "safe_field": "ok",
    }

    sanitized = sanitize_log_data(data)

    # Body fields should be replaced with warning
    assert sanitized["body"] == BODY_LOG_WARNING
    assert sanitized["request_body"] == BODY_LOG_WARNING
    assert sanitized["response_body"] == BODY_LOG_WARNING
    assert sanitized["data"] == BODY_LOG_WARNING
    assert sanitized["payload"] == BODY_LOG_WARNING
    assert sanitized["content"] == BODY_LOG_WARNING

    # Safe fields should remain
    assert sanitized["safe_field"] == "ok"


def test_sanitize_log_data_case_insensitive() -> None:
    """Test that sanitization is case-insensitive."""
    data = {
        "BODY": "sensitive",
        "Request_Body": "sensitive",
        "RESPONSE_DATA": "sensitive",
        "safe": "ok",
    }

    sanitized = sanitize_log_data(data)

    # Should catch case variations
    assert sanitized["BODY"] == BODY_LOG_WARNING
    assert sanitized["Request_Body"] == BODY_LOG_WARNING
    assert sanitized["RESPONSE_DATA"] == BODY_LOG_WARNING
    assert sanitized["safe"] == "ok"
