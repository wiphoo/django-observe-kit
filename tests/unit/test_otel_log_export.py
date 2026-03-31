"""Unit tests for OTEL log export wired up by init_tracing()."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_otel_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk._logs import LoggingHandler

    from observe_kit.otel import config as otel_config

    monkeypatch.setattr(otel_config, "_TRACING_INITIALIZED", False)
    monkeypatch.setattr(otel_config, "_LOG_EXPORT_INITIALIZED", False)

    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)

    yield

    root_logger.handlers = [
        handler
        for handler in root_logger.handlers
        if not (
            isinstance(handler, LoggingHandler)
            and getattr(handler, otel_config._OTEL_LOG_HANDLER_ATTR, False)
            and handler not in original_handlers
        )
    ]


def test_init_tracing_sets_logger_provider() -> None:
    """init_tracing() sets a global LoggerProvider."""
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider

    from observe_kit.otel.config import init_tracing

    with patch("observe_kit.otel.config.OTLPSpanExporter"):
        with patch("observe_kit.otel.config.OTLPLogExporter"):
            init_tracing(service_name="test-svc", endpoint="http://localhost:4318")

    provider = get_logger_provider()
    assert isinstance(provider, LoggerProvider)


def test_init_tracing_adds_logging_handler() -> None:
    """init_tracing() adds a LoggingHandler to the root logger."""
    from opentelemetry.sdk._logs import LoggingHandler

    from observe_kit.otel.config import init_tracing

    root_logger = logging.getLogger()

    with patch("observe_kit.otel.config.OTLPSpanExporter"):
        with patch("observe_kit.otel.config.OTLPLogExporter"):
            init_tracing(service_name="test-svc2", endpoint="http://localhost:4318")

    otel_handlers = [h for h in root_logger.handlers if isinstance(h, LoggingHandler)]
    assert len(otel_handlers) >= 1


def test_init_otel_log_export_uses_same_endpoint() -> None:
    """_init_otel_log_export passes the endpoint to OTLPLogExporter."""
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    mock_exporter = MagicMock()
    with patch("observe_kit.otel.config.OTLPLogExporter", return_value=mock_exporter) as mock_cls:
        with patch("observe_kit.otel.config.set_logger_provider"):
            resource = Resource.create({"service.name": "svc"})
            _init_otel_log_export(resource=resource, endpoint="http://otel:4318")

    mock_cls.assert_called_once_with(endpoint="http://otel:4318")


def test_init_otel_log_export_uses_default_when_no_endpoint() -> None:
    """_init_otel_log_export uses OTLPLogExporter default when endpoint is None."""
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    with patch("observe_kit.otel.config.OTLPLogExporter") as mock_cls:
        with patch("observe_kit.otel.config.set_logger_provider"):
            resource = Resource.create({"service.name": "svc"})
            _init_otel_log_export(resource=resource, endpoint=None)

    # Should be called with no endpoint arg so the exporter uses its own default.
    mock_cls.assert_called_once_with()


def test_init_otel_log_export_shares_resource_with_tracer() -> None:
    """LoggerProvider uses the same Resource as TracerProvider."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    resource = Resource.create({"service.name": "shared-svc"})
    with patch("observe_kit.otel.config.OTLPLogExporter"):
        with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
            with patch("observe_kit.otel.config.set_logger_provider") as mock_set:
                _init_otel_log_export(resource=resource, endpoint=None)

    # The LoggerProvider passed to set_logger_provider should carry the same resource.
    provider_arg: LoggerProvider = mock_set.call_args[0][0]
    assert isinstance(provider_arg, LoggerProvider)
    assert provider_arg.resource == resource


def test_init_tracing_is_idempotent() -> None:
    from observe_kit.otel.config import init_tracing

    with patch("observe_kit.otel.config.OTLPSpanExporter") as mock_span:
        with patch("observe_kit.otel.config.OTLPLogExporter") as mock_log:
            init_tracing(service_name="test-svc", endpoint="http://localhost:4318")
            init_tracing(service_name="test-svc", endpoint="http://localhost:4318")

    mock_span.assert_called_once_with(endpoint="http://localhost:4318")
    mock_log.assert_called_once_with(endpoint="http://localhost:4318")
