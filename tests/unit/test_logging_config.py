"""Unit tests for logging configuration."""

from unittest.mock import Mock, patch

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_configure_logging_with_extra() -> None:
    """Test configure_logging with extra parameter."""
    from observe_kit.logging.config import configure_logging

    extra = {
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": "/tmp/test.log",
                "formatter": "json",
            }
        },
        "loggers": {"test_logger": {"handlers": ["file"], "level": "DEBUG"}},
    }

    with patch("observe_kit.logging.config.logging.config.dictConfig") as mock_dict_config:
        configure_logging(level="INFO", extra=extra)

        call_args = mock_dict_config.call_args[0][0]
        assert "file" in call_args["handlers"]
        assert "test_logger" in call_args["loggers"]


def test_configure_logging_with_pii_level() -> None:
    """Test configure_logging with pii_level parameter."""
    from observe_kit.logging.config import configure_logging
    from observe_kit.pii_rules import get_pii_config

    with patch("observe_kit.logging.config.logging.config.dictConfig"):
        configure_logging(level="INFO", pii_level="BASIC")

        config = get_pii_config()
        assert config.get_level("logs") == "BASIC"


def test_configure_logging_with_pii_levels() -> None:
    """Test configure_logging with pii_levels parameter."""
    from observe_kit.logging.config import configure_logging
    from observe_kit.pii_rules import get_pii_config

    pii_levels = {"logs": "SENSITIVE", "otel": "BASIC", "sentry": "NONE"}

    with patch("observe_kit.logging.config.logging.config.dictConfig"):
        configure_logging(level="INFO", pii_levels=pii_levels)

        config = get_pii_config()
        assert config.get_level("logs") == "SENSITIVE"
        assert config.get_level("otel") == "BASIC"
        assert config.get_level("sentry") == "NONE"


def test_log_request_complete() -> None:
    """Test log_request_complete function."""
    from logging import Logger

    from observe_kit.logging.config import log_request_complete

    mock_logger = Mock(spec=Logger)
    log_request_complete(mock_logger, status=200, duration=0.5)

    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    assert call_args[0][0] == "request_complete"
    assert call_args[1]["extra"]["extra"]["status"] == 200
    assert call_args[1]["extra"]["extra"]["duration"] == 0.5
