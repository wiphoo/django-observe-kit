"""Edge case tests for Sentry configuration."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_validate_dsn_empty() -> None:
    """Test _validate_dsn with empty string."""
    from observe_kit.sentry.config import ConfigurationError, _validate_dsn

    with pytest.raises(ConfigurationError, match="must be a non-empty string"):
        _validate_dsn("")


def test_validate_dsn_invalid_url() -> None:
    """Test _validate_dsn with invalid URL."""
    from observe_kit.sentry.config import ConfigurationError, _validate_dsn

    with pytest.raises(ConfigurationError, match="http:// or https://"):
        _validate_dsn("not-a-url")


def test_validate_dsn_no_http() -> None:
    """Test _validate_dsn without http/https."""
    from observe_kit.sentry.config import ConfigurationError, _validate_dsn

    with pytest.raises(ConfigurationError, match="http:// or https://"):
        _validate_dsn("ftp://sentry.io/123")


def test_validate_environment_empty() -> None:
    """Test _validate_environment with empty string."""
    from observe_kit.sentry.config import ConfigurationError, _validate_environment

    with pytest.raises(ConfigurationError, match="must be a non-empty string"):
        _validate_environment("")


def test_validate_environment_too_long() -> None:
    """Test _validate_environment with too long name."""
    from observe_kit.sentry.config import ConfigurationError, _validate_environment

    long_env = "a" * 65
    with pytest.raises(ConfigurationError, match="64 characters or less"):
        _validate_environment(long_env)


def test_validate_traces_sample_rate_too_high() -> None:
    """Test _validate_traces_sample_rate with value > 1.0."""
    from observe_kit.sentry.config import ConfigurationError, _validate_traces_sample_rate

    with pytest.raises(ConfigurationError, match="between 0.0 and 1.0"):
        _validate_traces_sample_rate(1.5)


def test_validate_traces_sample_rate_negative() -> None:
    """Test _validate_traces_sample_rate with negative value."""
    from observe_kit.sentry.config import ConfigurationError, _validate_traces_sample_rate

    with pytest.raises(ConfigurationError, match="between 0.0 and 1.0"):
        _validate_traces_sample_rate(-0.1)


def test_scrub_event() -> None:
    """Test scrub_event function."""
    from observe_kit.pii_rules import PiiLevel
    from observe_kit.sentry.config import scrub_event

    event = {"request": {"headers": {"Authorization": "Bearer token123", "X-API-Key": "key456"}}}

    result = scrub_event(event, None, PiiLevel.BASIC)

    assert "request" in result
    assert "headers" in result["request"]
    # Headers should be sanitized
    headers = result["request"]["headers"]
    assert "Authorization" not in headers or headers["Authorization"] != "Bearer token123"


def test_init_sentry_with_pii_level() -> None:
    """Test init_sentry with explicit pii_level."""
    from observe_kit.pii_rules import PiiLevel
    from observe_kit.sentry.config import init_sentry

    with patch("observe_kit.sentry.config.sentry_sdk") as mock_sentry:
        init_sentry(
            dsn="https://test@sentry.io/123", environment="test", pii_level=PiiLevel.SENSITIVE
        )

        mock_sentry.init.assert_called_once()
        call_kwargs = mock_sentry.init.call_args[1]
        assert call_kwargs["dsn"] == "https://test@sentry.io/123"
        assert call_kwargs["environment"] == "test"


def test_init_sentry_with_before_send() -> None:
    """Test init_sentry with custom before_send."""
    from observe_kit.sentry.config import init_sentry

    def custom_before_send(event: dict, hint: dict) -> dict:
        return event

    with patch("observe_kit.sentry.config.sentry_sdk") as mock_sentry:
        init_sentry(
            dsn="https://test@sentry.io/123", environment="test", before_send=custom_before_send
        )

        mock_sentry.init.assert_called_once()
        call_kwargs = mock_sentry.init.call_args[1]
        assert call_kwargs["before_send"] == custom_before_send
