from __future__ import annotations

import logging
import logging.config
from typing import Any, Dict, Optional

from pythonjsonlogger import jsonlogger

from .filters import RequestContextFilter


class RequestFormatter(jsonlogger.JsonFormatter):
    """Formatter that leaves message intact while providing structured defaults."""

    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record.setdefault("level", record.levelname)
        log_record.setdefault("logger", record.name)


def configure_logging(level: str = "INFO", pii_level: str = "BASIC", extra: Optional[Dict[str, Any]] = None) -> None:
    """Configure JSON logging with request context filter."""

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
    logging.config.dictConfig(logging_config)


def log_request_complete(logger: logging.Logger, **fields: Any) -> None:
    logger.info("request_complete", extra={"extra": fields})
