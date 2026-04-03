"""Unit tests for ObserveKitConfig.ready() auto-initialisation."""

from __future__ import annotations

from unittest.mock import patch

from observe_kit.settings import ObserveKitSettings
from tests.unit.conftest import make_observe_kit_settings as _make_cfg


def _run_ready(cfg: ObserveKitSettings):
    """Patch init functions at their source modules, call ready(), return mocks."""
    with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
        with patch("observe_kit.logging.configure_logging") as mock_log:
            with patch("observe_kit.otel.init_tracing") as mock_trace:
                with patch("observe_kit.sentry.init_sentry") as mock_sentry:
                    from observe_kit.apps import ObserveKitConfig

                    app = ObserveKitConfig.create("observe_kit")
                    app.ready()
    return mock_log, mock_trace, mock_sentry


def test_ready_calls_configure_logging_always() -> None:
    mock_log, _, _ = _run_ready(_make_cfg())
    mock_log.assert_called_once()


def test_ready_is_noop_when_observe_kit_settings_absent() -> None:
    mock_log, mock_trace, mock_sentry = _run_ready(_make_cfg(configured=False))
    mock_log.assert_not_called()
    mock_trace.assert_not_called()
    mock_sentry.assert_not_called()


def test_ready_skips_init_tracing_when_no_service_name() -> None:
    _, mock_trace, _ = _run_ready(_make_cfg(service_name=None))
    mock_trace.assert_not_called()


def test_ready_calls_init_tracing_when_service_name_set() -> None:
    _, mock_trace, _ = _run_ready(
        _make_cfg(service_name="my-app", otel_endpoint="http://localhost:4318")
    )
    mock_trace.assert_called_once_with(
        service_name="my-app", endpoint="http://localhost:4318", sample_rate=None
    )


def test_ready_skips_init_sentry_when_no_dsn() -> None:
    _, _, mock_sentry = _run_ready(_make_cfg(sentry_dsn=None))
    mock_sentry.assert_not_called()


def test_ready_calls_init_sentry_when_dsn_set() -> None:
    _, _, mock_sentry = _run_ready(
        _make_cfg(
            sentry_dsn="https://key@sentry.io/1",
            sentry_environment="staging",
            sentry_traces_sample_rate=0.1,
        )
    )
    mock_sentry.assert_called_once_with(
        dsn="https://key@sentry.io/1", environment="staging", traces_sample_rate=0.1
    )


def test_ready_is_noop_when_disabled() -> None:
    mock_log, mock_trace, mock_sentry = _run_ready(
        _make_cfg(enabled=False, service_name="svc", sentry_dsn="https://key@sentry.io/1")
    )
    mock_log.assert_not_called()
    mock_trace.assert_not_called()
    mock_sentry.assert_not_called()


def test_ready_passes_effective_pii_levels_to_configure_logging() -> None:
    mock_log, _, _ = _run_ready(_make_cfg(pii_level="SENSITIVE"))
    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["pii_levels"] == {
        "logs": "SENSITIVE",
        "otel": "SENSITIVE",
        "sentry": "SENSITIVE",
        "audit": "SENSITIVE",
    }
