"""Unit tests for OTEL log export wired up by init_tracing()."""

from __future__ import annotations

import logging
from types import SimpleNamespace
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
            with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
                init_tracing(service_name="test-svc2", endpoint="http://localhost:4318")

    otel_handlers = [h for h in root_logger.handlers if isinstance(h, LoggingHandler)]
    assert len(otel_handlers) >= 1


def test_init_tracing_reinstalls_logging_handler_when_missing() -> None:
    """A repeated init_tracing() call should restore the OTEL handler if it was removed."""
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler

    from observe_kit.otel import config as otel_config
    from observe_kit.otel.config import init_tracing

    root_logger = logging.getLogger()

    with patch("observe_kit.otel.config.OTLPSpanExporter"):
        with patch("observe_kit.otel.config.OTLPLogExporter"):
            with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
                init_tracing(service_name="test-svc2", endpoint="http://localhost:4318")

    root_logger.handlers = [
        handler
        for handler in root_logger.handlers
        if not getattr(handler, otel_config._OTEL_LOG_HANDLER_ATTR, False)
    ]

    with patch("observe_kit.otel.config.get_logger_provider") as mock_provider:
        mock_provider.return_value = LoggerProvider()
        init_tracing(service_name="test-svc2", endpoint="http://localhost:4318")

    otel_handlers = [
        handler
        for handler in root_logger.handlers
        if isinstance(handler, LoggingHandler)
        and getattr(handler, otel_config._OTEL_LOG_HANDLER_ATTR, False)
    ]
    assert len(otel_handlers) == 1


def test_init_otel_log_export_uses_same_endpoint() -> None:
    """_init_otel_log_export derives the logs endpoint from the OTLP HTTP base URL."""
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    mock_exporter = MagicMock()
    with patch("observe_kit.otel.config.OTLPLogExporter", return_value=mock_exporter) as mock_cls:
        with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
            with patch("observe_kit.otel.config.set_logger_provider"):
                resource = Resource.create({"service.name": "svc"})
                _init_otel_log_export(resource=resource, endpoint="http://otel:4318")

    mock_cls.assert_called_once_with(endpoint="http://otel:4318/v1/logs")


def test_init_otel_log_export_uses_default_when_no_endpoint() -> None:
    """_init_otel_log_export uses OTLPLogExporter default when endpoint is None."""
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    with patch("observe_kit.otel.config.OTLPLogExporter") as mock_cls:
        with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
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


def test_init_otel_log_export_reuses_existing_logger_provider() -> None:
    """An existing LoggerProvider should get the OTLP processor instead of being replaced."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    existing_provider = LoggerProvider()
    resource = Resource.create({"service.name": "shared-svc"})

    with patch("observe_kit.otel.config.get_logger_provider", return_value=existing_provider):
        with patch("observe_kit.otel.config.set_logger_provider") as mock_set:
            _init_otel_log_export(resource=resource, endpoint="http://otel:4318")

    processors = existing_provider._multi_log_record_processor._log_record_processors
    assert len(processors) == 1
    mock_set.assert_not_called()


def test_init_otel_log_export_does_not_duplicate_existing_otlp_processor() -> None:
    """An existing OTLP batch processor on the provider should not be added twice."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.resources import Resource

    from observe_kit.otel.config import _init_otel_log_export

    existing_provider = LoggerProvider()
    resource = Resource.create({"service.name": "shared-svc"})
    with patch("observe_kit.otel.config.get_logger_provider", return_value=existing_provider):
        _init_otel_log_export(resource=resource, endpoint="http://otel:4318")

    before = len(existing_provider._multi_log_record_processor._log_record_processors)

    from observe_kit.otel import config as otel_config

    otel_config._LOG_EXPORT_INITIALIZED = False

    with patch("observe_kit.otel.config.get_logger_provider", return_value=existing_provider):
        _init_otel_log_export(resource=resource, endpoint="http://otel:4318")

    after = len(existing_provider._multi_log_record_processor._log_record_processors)
    assert before == after


def test_init_tracing_is_idempotent() -> None:
    from observe_kit.otel.config import init_tracing

    with patch("observe_kit.otel.config.OTLPSpanExporter") as mock_span:
        with patch("observe_kit.otel.config.OTLPLogExporter") as mock_log:
            with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
                init_tracing(service_name="test-svc", endpoint="http://localhost:4318")
                init_tracing(service_name="test-svc", endpoint="http://localhost:4318")

    mock_span.assert_called_once_with(endpoint="http://localhost:4318/v1/traces")
    mock_log.assert_called_once_with(endpoint="http://localhost:4318/v1/logs")


def test_init_tracing_preserves_explicit_signal_endpoint() -> None:
    from observe_kit.otel.config import init_tracing

    with patch("observe_kit.otel.config.OTLPSpanExporter") as mock_span:
        with patch("observe_kit.otel.config.OTLPLogExporter") as mock_log:
            with patch("observe_kit.otel.config.get_logger_provider", return_value=object()):
                init_tracing(service_name="test-svc", endpoint="http://localhost:4318/v1/traces")

    mock_span.assert_called_once_with(endpoint="http://localhost:4318/v1/traces")
    mock_log.assert_called_once_with(endpoint="http://localhost:4318/v1/logs")


def test_otel_log_record_sanitizer_stringifies_unsupported_extra() -> None:
    """_SafeOTelLogHandler coerces unsupported extras on a *copy* of the record."""
    from unittest.mock import patch

    from observe_kit.otel.config import _SafeOTelLogHandler

    record = logging.makeLogRecord(
        {
            "msg": "request warning",
            "levelno": logging.WARNING,
            "levelname": "WARNING",
            "request": SimpleNamespace(path="/missing"),
        }
    )
    original_request = record.request  # keep reference to the original object

    handler = _SafeOTelLogHandler.__new__(_SafeOTelLogHandler)

    emitted: list[logging.LogRecord] = []

    with patch.object(
        _SafeOTelLogHandler.__bases__[0], "emit", side_effect=lambda r: emitted.append(r)
    ):
        _SafeOTelLogHandler.emit(handler, record)

    assert len(emitted) == 1
    emitted_record = emitted[0]
    # The emitted copy has the value stringified.
    assert isinstance(emitted_record.request, str)
    assert "path='/missing'" in emitted_record.request
    # The original record is NOT mutated.
    assert record.request is original_request
