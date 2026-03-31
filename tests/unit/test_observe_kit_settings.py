"""Unit tests for observe_kit.settings — ObserveKitSettings loader."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

# ── Defaults ──────────────────────────────────────────────────────────────────


def test_defaults_when_observe_kit_absent() -> None:
    """All defaults are applied when OBSERVE_KIT setting is absent."""
    from observe_kit.settings import get_observe_kit_settings

    # Django is configured in conftest.py without OBSERVE_KIT
    cfg = get_observe_kit_settings()
    assert cfg.configured is False
    assert cfg.service_name is None
    assert cfg.otel_endpoint is None
    assert cfg.log_level == "INFO"
    assert cfg.pii_level == "BASIC"
    assert cfg.pii_levels is None
    assert cfg.sentry_dsn is None
    assert cfg.sentry_environment == "production"
    assert cfg.sentry_traces_sample_rate == 0.0
    assert cfg.enabled is True
    assert cfg.db_tracking is True


# ── Dict values ───────────────────────────────────────────────────────────────


def test_dict_service_name_overrides_default(observe_kit_settings: Any) -> None:
    from observe_kit.settings import get_observe_kit_settings

    with observe_kit_settings({"SERVICE_NAME": "my-svc"}):
        cfg = get_observe_kit_settings()
        assert cfg.configured is True
        assert cfg.service_name == "my-svc"


def test_dict_all_keys(observe_kit_settings: Any) -> None:
    with observe_kit_settings(
        {
            "SERVICE_NAME": "svc",
            "OTEL_ENDPOINT": "http://otel:4318",
            "LOG_LEVEL": "DEBUG",
            "PII_LEVEL": "SENSITIVE",
            "PII_LEVELS": {"logs": "NONE", "otel": "BASIC"},
            "SENTRY_DSN": "https://key@sentry.io/1",
            "SENTRY_ENVIRONMENT": "staging",
            "SENTRY_TRACES_SAMPLE_RATE": 0.5,
            "ENABLED": True,
            "DB_TRACKING": False,
        }
    ):
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.configured is True
        assert cfg.service_name == "svc"
        assert cfg.otel_endpoint == "http://otel:4318"
        assert cfg.log_level == "DEBUG"
        assert cfg.pii_level == "SENSITIVE"
        assert cfg.pii_levels == {"logs": "NONE", "otel": "BASIC"}
        assert cfg.sentry_dsn == "https://key@sentry.io/1"
        assert cfg.sentry_environment == "staging"
        assert cfg.sentry_traces_sample_rate == 0.5
        assert cfg.enabled is True
        assert cfg.db_tracking is False


# ── Env var fallbacks ─────────────────────────────────────────────────────────


def test_env_var_service_name_fallback(observe_kit_settings: Any) -> None:
    with observe_kit_settings({}):
        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "env-svc"}, clear=False):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
            assert cfg.service_name == "env-svc"


def test_env_var_otel_endpoint_fallback(observe_kit_settings: Any) -> None:
    with observe_kit_settings({}):
        with patch.dict(
            os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, clear=False
        ):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
            assert cfg.otel_endpoint == "http://collector:4318"


def test_dict_takes_precedence_over_env_var(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"SERVICE_NAME": "dict-svc"}):
        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "env-svc"}, clear=False):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
            assert cfg.service_name == "dict-svc"


# ── Type coercions ────────────────────────────────────────────────────────────


def test_sample_rate_string_coercion(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"SENTRY_TRACES_SAMPLE_RATE": "0.25"}):
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.sentry_traces_sample_rate == 0.25


def test_enabled_false_string(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"ENABLED": "false"}):
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.enabled is False


def test_log_level_normalised_to_uppercase(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"LOG_LEVEL": "debug"}):
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.log_level == "DEBUG"


# ── effective_pii_levels ──────────────────────────────────────────────────────


def test_effective_pii_levels_expands_global() -> None:
    from observe_kit.settings import get_observe_kit_settings

    cfg = get_observe_kit_settings()
    # pii_levels not set → global pii_level (BASIC) is broadcast to all sinks
    levels = cfg.effective_pii_levels
    assert all(v == "BASIC" for v in levels.values())
    assert set(levels) == {"logs", "otel", "sentry", "audit"}


def test_effective_pii_levels_uses_per_sink_when_set(observe_kit_settings: Any) -> None:
    per_sink = {"logs": "NONE", "otel": "SENSITIVE", "sentry": "BASIC", "audit": "NONE"}
    with observe_kit_settings({"PII_LEVELS": per_sink}):
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.effective_pii_levels == per_sink


# ── Master switch ─────────────────────────────────────────────────────────────


def test_enabled_false_bool(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"ENABLED": False}):
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.enabled is False
