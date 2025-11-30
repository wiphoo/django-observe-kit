"""Tests for per-sink PII configuration."""

from observe_kit.pii_rules import PiiConfig, PiiLevel, get_pii_config, set_pii_config


def test_pii_config_creation() -> None:
    """Test creating PII configuration."""
    config = PiiConfig(levels={"logs": "BASIC", "sentry": "SENSITIVE"})

    assert config.get_level("logs") == PiiLevel.BASIC
    assert config.get_level("sentry") == PiiLevel.SENSITIVE
    assert config.get_level("otel") == PiiLevel.BASIC  # Default


def test_pii_config_get_set() -> None:
    """Test getting and setting PII levels."""
    config = PiiConfig()

    # Test get with default
    assert config.get_level("logs") == PiiLevel.BASIC

    # Test set
    config.set_level("logs", "SENSITIVE")
    assert config.get_level("logs") == PiiLevel.SENSITIVE


def test_pii_config_global() -> None:
    """Test global PII configuration."""
    original_config = get_pii_config()

    # Create new config
    new_config = PiiConfig(levels={"logs": "SENSITIVE"})
    set_pii_config(new_config)

    # Verify it's set
    current_config = get_pii_config()
    assert current_config.get_level("logs") == PiiLevel.SENSITIVE

    # Restore original
    set_pii_config(original_config)


def test_pii_config_invalid_sink() -> None:
    """Test that invalid sinks use default level."""
    config = PiiConfig()
    # Invalid sink should return BASIC as fallback
    assert config.get_level("invalid_sink") == PiiLevel.BASIC
