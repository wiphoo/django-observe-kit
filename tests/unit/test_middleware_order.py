"""Tests for the observe_kit middleware-order validator."""

from __future__ import annotations

from observe_kit.apps import _CANONICAL_MIDDLEWARE_ORDER, _validate_middleware_order

OTEL = "observe_kit.otel.middleware.TraceContextMiddleware"
LOGGING = "observe_kit.logging.middleware.RequestLoggingMiddleware"
METRICS = "observe_kit.metrics.middleware.PrometheusRequestMiddleware"
CONTEXT = "observe_kit.context_middleware.RequestContextMiddleware"
USER = "observe_kit.context_middleware.UserLoggingContextMiddleware"
DRF = "observe_kit.drf.integration.DRFIntegrationMiddleware"
SENTRY = "observe_kit.sentry.middleware.SentryContextMiddleware"

CANONICAL = [OTEL, LOGGING, METRICS, CONTEXT, USER, DRF, SENTRY]


def test_canonical_order_emits_no_warnings() -> None:
    assert _validate_middleware_order(CANONICAL) == []


def test_canonical_with_unrelated_django_middleware_around_it() -> None:
    middleware = [
        "django.middleware.security.SecurityMiddleware",
        *CANONICAL,
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ]
    assert _validate_middleware_order(middleware) == []


def test_no_observe_kit_middleware_present_emits_no_warnings() -> None:
    # User using observe_kit programmatically without the middlewares.
    middleware = [
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
    ]
    assert _validate_middleware_order(middleware) == []


def test_empty_middleware_emits_no_warnings() -> None:
    assert _validate_middleware_order([]) == []


def test_swapped_context_above_logging_warns() -> None:
    # The classic foot-gun: RequestContextMiddleware above RequestLoggingMiddleware.
    middleware = [OTEL, CONTEXT, LOGGING, METRICS, USER, DRF, SENTRY]
    warnings = _validate_middleware_order(middleware)
    assert any(
        "RequestLoggingMiddleware" in w and "RequestContextMiddleware" in w for w in warnings
    )


def test_fully_reversed_order_produces_many_warnings() -> None:
    reversed_order = list(reversed(CANONICAL))
    warnings = _validate_middleware_order(reversed_order)
    # 7 entries → at least 6 inversions (every adjacent pair is wrong).
    assert len(warnings) >= 6


def test_missing_required_middleware_warns() -> None:
    # Drop the logging middleware while keeping others.
    middleware = [m for m in CANONICAL if m != LOGGING]
    warnings = _validate_middleware_order(middleware)
    assert any("missing required entry" in w and "RequestLoggingMiddleware" in w for w in warnings)


def test_missing_optional_drf_does_not_warn() -> None:
    middleware = [m for m in CANONICAL if m != DRF]
    warnings = _validate_middleware_order(middleware)
    # No "missing required entry" mentions for DRFIntegrationMiddleware.
    assert not any("DRFIntegrationMiddleware" in w and "missing" in w for w in warnings)
    # And no order warnings since the remaining items stay in canonical order.
    assert warnings == []


def test_canonical_order_constant_is_well_formed() -> None:
    # Guardrail: each entry is (str, bool); no duplicates; required-flags reasonable.
    paths = [path for path, _ in _CANONICAL_MIDDLEWARE_ORDER]
    assert len(paths) == len(set(paths)), "canonical order has duplicates"
    # At least one optional entry exists (DRF).
    assert any(not req for _, req in _CANONICAL_MIDDLEWARE_ORDER)


def test_only_one_middleware_present_emits_only_missing_warnings() -> None:
    # Single observe_kit middleware in the list — no order-pairs to invert, but
    # every other required entry should be flagged as missing.
    warnings = _validate_middleware_order([OTEL])
    # Every warning must be a "missing required" one (no order-inversion text).
    assert all("missing required entry" in w for w in warnings)
    # The 5 other required entries must each be flagged.
    expected_missing = {LOGGING, METRICS, CONTEXT, USER, SENTRY}
    flagged = {path for w in warnings for path in expected_missing if path in w}
    assert flagged == expected_missing


# ---------------------------------------------------------------------------
# DRF / Sentry ordering is no longer enforced (codex P2)
# ---------------------------------------------------------------------------


def test_drf_after_sentry_does_not_warn() -> None:
    """DRF only implements process_view, Sentry only process_request, so the
    relative order between them is behaviorally irrelevant and should not
    produce a startup warning."""
    middleware = [OTEL, LOGGING, METRICS, CONTEXT, USER, SENTRY, DRF]
    warnings = _validate_middleware_order(middleware)
    # No warning that mentions both DRF and Sentry.
    drf_sentry = [w for w in warnings if "DRFIntegrationMiddleware" in w and "Sentry" in w]
    assert drf_sentry == []
    # And in fact this configuration is fully clean.
    assert warnings == []


# ---------------------------------------------------------------------------
# Deterministic missing-required ordering (qodo bug #2)
# ---------------------------------------------------------------------------


def test_missing_required_warnings_are_in_canonical_order() -> None:
    """Iterating a set used to produce non-deterministic missing-required
    warning order. Now iterates the canonical tuple, so output is stable."""
    # Provide only OTEL — every other required entry is missing.
    warnings = _validate_middleware_order([OTEL])
    missing_warnings = [w for w in warnings if "missing required entry" in w]

    # Extract which middleware each warning references, in order.
    canonical_order = [LOGGING, METRICS, CONTEXT, USER, SENTRY]
    flagged_in_order = []
    for w in missing_warnings:
        for path in canonical_order:
            if path in w:
                flagged_in_order.append(path)
                break

    assert flagged_in_order == canonical_order


# ---------------------------------------------------------------------------
# Opt-out behaviour at ObserveKitConfig.ready() (qodo gap #1)
# ---------------------------------------------------------------------------


def test_ready_skips_validator_when_opt_out_is_false() -> None:
    """OBSERVE_KIT['VALIDATE_MIDDLEWARE_ORDER']=False must prevent the
    validator from running at startup."""
    from unittest.mock import patch

    from observe_kit.apps import ObserveKitConfig
    from tests.unit.conftest import make_observe_kit_settings

    cfg = make_observe_kit_settings(validate_middleware_order=False)
    with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
        with patch("observe_kit.apps._validate_middleware_order") as mock_validator:
            with patch("observe_kit.logging.configure_logging"):
                with patch("observe_kit.otel.init_tracing"):
                    with patch("observe_kit.sentry.init_sentry"):
                        app = ObserveKitConfig.create("observe_kit")
                        app.ready()
    mock_validator.assert_not_called()


def test_ready_runs_validator_when_opt_out_is_true_default() -> None:
    """Default config (validate_middleware_order=True) runs the validator."""
    from unittest.mock import patch

    from observe_kit.apps import ObserveKitConfig
    from tests.unit.conftest import make_observe_kit_settings

    cfg = make_observe_kit_settings(validate_middleware_order=True)
    with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
        with patch(
            "observe_kit.apps._validate_middleware_order", return_value=[]
        ) as mock_validator:
            with patch("observe_kit.logging.configure_logging"):
                with patch("observe_kit.otel.init_tracing"):
                    with patch("observe_kit.sentry.init_sentry"):
                        app = ObserveKitConfig.create("observe_kit")
                        app.ready()
    mock_validator.assert_called_once()


# ---------------------------------------------------------------------------
# Strict bool parsing for the opt-out flag (qodo bug #3)
# ---------------------------------------------------------------------------


def test_strict_bool_parses_zero_as_false(monkeypatch) -> None:
    """`OBSERVE_KIT_VALIDATE_MIDDLEWARE_ORDER=0` must disable the validator."""
    import pytest
    from django.test import override_settings

    from observe_kit.settings import get_observe_kit_settings

    monkeypatch.setenv("OBSERVE_KIT_VALIDATE_MIDDLEWARE_ORDER", "0")
    with override_settings(OBSERVE_KIT={}):
        cfg = get_observe_kit_settings()
    assert cfg.validate_middleware_order is False
    del pytest  # silence ruff F401


def test_strict_bool_parses_no_as_false(monkeypatch) -> None:
    from django.test import override_settings

    from observe_kit.settings import get_observe_kit_settings

    monkeypatch.setenv("OBSERVE_KIT_VALIDATE_MIDDLEWARE_ORDER", "no")
    with override_settings(OBSERVE_KIT={}):
        cfg = get_observe_kit_settings()
    assert cfg.validate_middleware_order is False


def test_strict_bool_unknown_value_falls_back_to_default_true(monkeypatch) -> None:
    """Unrecognised strings preserve the secure default (True for the validator)."""
    from django.test import override_settings

    from observe_kit.settings import get_observe_kit_settings

    monkeypatch.setenv("OBSERVE_KIT_VALIDATE_MIDDLEWARE_ORDER", "maybe")
    with override_settings(OBSERVE_KIT={}):
        cfg = get_observe_kit_settings()
    assert cfg.validate_middleware_order is True
