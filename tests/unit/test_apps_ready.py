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


# ---------------------------------------------------------------------------
# Middleware-order validator opt-out at the ready() entrypoint (PR #8 / #7)
# ---------------------------------------------------------------------------


def _run_ready_capturing_apps_warnings(
    cfg: ObserveKitSettings, middleware: list[str] | None = None
) -> tuple[object, list[str]]:
    """Call ready() with a controlled MIDDLEWARE and capture logger.warning calls.

    Returns (mock_validator, captured_warnings) where captured_warnings is the
    list of message strings logged via ``observe_kit.apps.logger.warning``.
    Other init functions are patched so this test doesn't exercise logging,
    OTEL, or Sentry initialisation paths.
    """
    from django.test import override_settings

    captured: list[str] = []

    def _capture(msg: object, *args: object, **kwargs: object) -> None:
        # Mirror logger.warning's printf-style API: format if args were passed.
        if args:
            captured.append(str(msg) % args)
        else:
            captured.append(str(msg))

    middleware_setting = middleware if middleware is not None else []

    with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
        with patch("observe_kit.logging.configure_logging"):
            with patch("observe_kit.otel.init_tracing"):
                with patch("observe_kit.sentry.init_sentry"):
                    with patch("observe_kit.apps._validate_middleware_order") as mock_v:
                        with patch("observe_kit.apps.logger") as mock_logger:
                            mock_logger.warning.side_effect = _capture
                            with override_settings(MIDDLEWARE=middleware_setting):
                                from observe_kit.apps import ObserveKitConfig

                                app = ObserveKitConfig.create("observe_kit")
                                app.ready()
    return mock_v, captured


def test_ready_skips_middleware_validator_when_opt_out_is_false() -> None:
    """OBSERVE_KIT['VALIDATE_MIDDLEWARE_ORDER']=False must:
    1. Prevent ``_validate_middleware_order`` from being called.
    2. Emit no validator warnings from ``observe_kit.apps.logger``.

    Setup uses a *broken* middleware order (Sentry above OTEL) so that if the
    validator were to run, it would log warnings. Asserting on the captured
    warning list proves the opt-out really suppresses startup output.
    """
    broken_middleware = [
        "observe_kit.sentry.middleware.SentryContextMiddleware",
        "observe_kit.otel.middleware.TraceContextMiddleware",
        "observe_kit.logging.middleware.RequestLoggingMiddleware",
        "observe_kit.metrics.middleware.PrometheusRequestMiddleware",
        "observe_kit.context_middleware.RequestContextMiddleware",
        "observe_kit.context_middleware.UserLoggingContextMiddleware",
    ]

    cfg = _make_cfg(validate_middleware_order=False)
    mock_validator, warnings_emitted = _run_ready_capturing_apps_warnings(
        cfg, middleware=broken_middleware
    )

    # 1. Validator helper must not be invoked.
    mock_validator.assert_not_called()
    # 2. No validator-shaped warnings ("MIDDLEWARE order — …", "missing required entry").
    validator_warnings = [
        w for w in warnings_emitted if "MIDDLEWARE order" in w or "missing required entry" in w
    ]
    assert validator_warnings == [], (
        f"Expected no validator warnings when opt-out is False; got: {validator_warnings!r}"
    )


def test_ready_runs_middleware_validator_when_opt_in_is_true() -> None:
    """Default (validate_middleware_order=True) must invoke the validator
    and log every warning it returns. This is the inverse coverage of the
    opt-out test."""
    sample_warnings = [
        "observe_kit: MIDDLEWARE order — 'X' (index 5) should appear before 'Y' (index 1).",
        "observe_kit: MIDDLEWARE is missing required entry 'Z'.",
    ]
    cfg = _make_cfg(validate_middleware_order=True)

    with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
        with patch("observe_kit.logging.configure_logging"):
            with patch("observe_kit.otel.init_tracing"):
                with patch("observe_kit.sentry.init_sentry"):
                    with patch(
                        "observe_kit.apps._validate_middleware_order", return_value=sample_warnings
                    ) as mock_v:
                        with patch("observe_kit.apps.logger") as mock_logger:
                            from observe_kit.apps import ObserveKitConfig

                            app = ObserveKitConfig.create("observe_kit")
                            app.ready()

    mock_v.assert_called_once()
    logged = [c.args[0] for c in mock_logger.warning.call_args_list]
    for expected in sample_warnings:
        assert expected in logged, f"validator warning {expected!r} not surfaced via logger"


def test_ready_validator_failure_does_not_break_startup() -> None:
    """A bug inside the validator must not prevent the rest of ready() from running.
    The wrapper logs via ``logger.exception`` but proceeds with logging/OTEL/Sentry
    initialisation."""
    cfg = _make_cfg(validate_middleware_order=True, service_name="svc")

    with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
        with patch("observe_kit.logging.configure_logging") as mock_log:
            with patch("observe_kit.otel.init_tracing") as mock_trace:
                with patch("observe_kit.sentry.init_sentry"):
                    with patch(
                        "observe_kit.apps._validate_middleware_order",
                        side_effect=RuntimeError("simulated validator bug"),
                    ):
                        from observe_kit.apps import ObserveKitConfig

                        app = ObserveKitConfig.create("observe_kit")
                        # Must not raise.
                        app.ready()

    # Downstream init still ran despite the validator failure.
    mock_log.assert_called_once()
    mock_trace.assert_called_once()
