"""Tests for configuration validation."""

import importlib.util
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("django"), reason="django not installed"
)


def test_otel_config_validation() -> None:
    """Test OTEL configuration validation."""
    from observe_kit.otel.config import ConfigurationError, init_tracing

    # Valid configuration should not raise
    with patch("observe_kit.otel.config.OTLPSpanExporter"):
        with patch("observe_kit.otel.config.OTLPLogExporter"):
            init_tracing(service_name="test-service")

    # Invalid service name should raise
    with pytest.raises(ConfigurationError):
        init_tracing(service_name="")

    # Invalid endpoint should raise
    with pytest.raises(ConfigurationError):
        init_tracing(service_name="test", endpoint="not-a-url")


def test_sentry_config_validation() -> None:
    """Test Sentry configuration validation."""
    from observe_kit.sentry.config import ConfigurationError, init_sentry

    # Invalid DSN should raise
    with pytest.raises(ConfigurationError):
        init_sentry(dsn="", environment="test")

    # Invalid environment should raise
    with pytest.raises(ConfigurationError):
        init_sentry(dsn="https://test@test.ingest.sentry.io/0", environment="")

    # Invalid sample rate should raise
    with pytest.raises(ConfigurationError):
        init_sentry(
            dsn="https://test@test.ingest.sentry.io/0",
            environment="test",
            traces_sample_rate=1.5,  # > 1.0
        )


def test_logging_config_validation() -> None:
    """Test logging configuration validation."""
    from observe_kit.logging.config import ConfigurationError, configure_logging

    # Invalid log level should raise
    with pytest.raises(ConfigurationError):
        configure_logging(level="INVALID_LEVEL")

    # Invalid PII level should raise
    with pytest.raises(ConfigurationError):
        configure_logging(level="INFO", pii_level="INVALID")

    # Invalid per-sink PII levels should raise
    with pytest.raises(ConfigurationError):
        configure_logging(level="INFO", pii_levels={"invalid_sink": "BASIC"})

    with pytest.raises(ConfigurationError):
        configure_logging(level="INFO", pii_levels={"logs": "INVALID"})
