from __future__ import annotations

import json
import logging
import logging.config
from typing import Any, Dict, Optional

from ..conf import BODY_REDACTED, PII_SINK_LOGS
from ..log_sanitize import redacted_record, sanitize_record
from ..otel.config import _OTEL_LOG_HANDLER_ATTR
from ..pii_rules import PiiConfig, PiiLevel, get_pii_config, set_pii_config
from .filters import RequestContextFilter

# Use new import location to avoid deprecation warning
# pythonjsonlogger.jsonlogger has been moved to pythonjsonlogger.json
try:
    from pythonjsonlogger import json as json_logger
except ImportError:
    # Fallback for older versions that don't have the new location
    import pythonjsonlogger.jsonlogger as json_logger  # type: ignore[no-redef]


class ConfigurationError(ValueError):
    """Raised when configuration is invalid."""

    pass


def _validate_log_level(level: str) -> None:
    """Validate logging level."""
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if not isinstance(level, str):
        raise ConfigurationError("level must be a string")
    if level.upper() not in valid_levels:
        raise ConfigurationError(f"level must be one of {valid_levels}, got '{level}'")


def _validate_pii_level(level: str) -> None:
    """Validate PII level."""
    valid_levels = {"NONE", "BASIC", "SENSITIVE"}
    if not isinstance(level, str):
        raise ConfigurationError("pii_level must be a string")
    if level.upper() not in valid_levels:
        raise ConfigurationError(f"pii_level must be one of {valid_levels}, got '{level}'")


def _validate_pii_levels(pii_levels: Dict[str, str]) -> None:
    """Validate per-sink PII levels."""
    valid_sinks = {"logs", "otel", "sentry", "audit"}
    valid_levels = {"NONE", "BASIC", "SENSITIVE"}

    if not isinstance(pii_levels, dict):
        raise ConfigurationError("pii_levels must be a dictionary")

    for sink, level in pii_levels.items():
        if sink not in valid_sinks:
            raise ConfigurationError(f"Invalid sink '{sink}', must be one of {valid_sinks}")
        if not isinstance(level, str) or level.upper() not in valid_levels:
            raise ConfigurationError(
                f"Invalid PII level '{level}' for sink '{sink}', must be one of {valid_levels}"
            )


def _normalize_pii_levels(pii_levels: Dict[str, str]) -> Dict[str, str]:
    return {sink: level.upper() for sink, level in pii_levels.items()}


class RequestFormatter(json_logger.JsonFormatter):
    """JSON formatter with structured defaults, sanitized at the ``logs`` PII level."""

    def format(self, record: logging.LogRecord) -> str:
        clean = sanitize_record(record, format_exception=self.formatException)
        try:
            return str(super().format(clean))
        except Exception:
            if get_pii_config().get_level(PII_SINK_LOGS) == PiiLevel.NONE:
                raise
            # Raising here makes ``Handler.handleError`` print the raw msg and args.
            try:
                return str(super().format(redacted_record(record)))
            except Exception:  # noqa: BLE001 - even the placeholder failed to format
                return json.dumps({"message": BODY_REDACTED})

    def add_fields(
        self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record.setdefault("level", record.levelname)
        log_record.setdefault("logger", record.name)


def configure_logging(
    level: str = "INFO",
    pii_level: Optional[str] = None,
    pii_levels: Optional[Dict[str, str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Configure JSON logging with request context filter.

    Args:
        level: Logging level (must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL)
        pii_level: Optional PII level for logs sink (deprecated, use pii_levels instead)
                  Must be one of: NONE, BASIC, SENSITIVE
        pii_levels: Optional dict mapping sink names to PII levels.
                   Valid sinks: 'logs', 'otel', 'sentry', 'audit'
                   Valid levels: 'NONE', 'BASIC', 'SENSITIVE'
        extra: Optional additional logging configuration dict

    Raises:
        ConfigurationError: If any configuration parameter is invalid
    """
    # Validate configuration
    _validate_log_level(level)

    if pii_levels:
        _validate_pii_levels(pii_levels)
        config = PiiConfig(levels=_normalize_pii_levels(pii_levels))
        set_pii_config(config)
    elif pii_level:
        _validate_pii_level(pii_level)
        # Backward compatibility: set logs sink level
        config = get_pii_config()
        config.set_level(PII_SINK_LOGS, pii_level.upper())
        set_pii_config(config)

    handlers: Dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_context"],
        }
    }
    filters = {"request_context": {"()": RequestContextFilter}}
    formatters = {"json": {"()": RequestFormatter}}
    logging_config: Dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": filters,
        "formatters": formatters,
        "handlers": handlers,
        "root": {"handlers": ["console"], "level": level},
    }
    if extra:
        logging_config.update(extra)

    root_logger = logging.getLogger()
    preserved_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, _OTEL_LOG_HANDLER_ATTR, False)
    ]
    logging.config.dictConfig(logging_config)
    for handler in preserved_handlers:
        if handler not in root_logger.handlers:
            root_logger.addHandler(handler)


def log_request_complete(logger: logging.Logger, **fields: Any) -> None:
    logger.info("request_complete", extra={"extra": fields})
