"""Unit tests for observe_kit.settings — ObserveKitSettings loader."""

from __future__ import annotations

import os
from unittest.mock import patch

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


def test_dict_service_name_overrides_default() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {"SERVICE_NAME": "my-svc"}  # type: ignore[attr-defined]
    try:
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.configured is True
        assert cfg.service_name == "my-svc"
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


def test_dict_all_keys() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {  # type: ignore[attr-defined]
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
    try:
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
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


# ── Env var fallbacks ─────────────────────────────────────────────────────────


def test_env_var_service_name_fallback() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {}  # type: ignore[attr-defined]
    try:
        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "env-svc"}, clear=False):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
            assert cfg.service_name == "env-svc"
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


def test_env_var_otel_endpoint_fallback() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {}  # type: ignore[attr-defined]
    try:
        with patch.dict(
            os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, clear=False
        ):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
            assert cfg.otel_endpoint == "http://collector:4318"
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


def test_dict_takes_precedence_over_env_var() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {"SERVICE_NAME": "dict-svc"}  # type: ignore[attr-defined]
    try:
        with patch.dict(os.environ, {"OTEL_SERVICE_NAME": "env-svc"}, clear=False):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
            assert cfg.service_name == "dict-svc"
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


# ── Type coercions ────────────────────────────────────────────────────────────


def test_sample_rate_string_coercion() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {"SENTRY_TRACES_SAMPLE_RATE": "0.25"}  # type: ignore[attr-defined]
    try:
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.sentry_traces_sample_rate == 0.25
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


def test_enabled_false_string() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {"ENABLED": "false"}  # type: ignore[attr-defined]
    try:
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.enabled is False
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


def test_log_level_normalised_to_uppercase() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {"LOG_LEVEL": "debug"}  # type: ignore[attr-defined]
    try:
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.log_level == "DEBUG"
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


# ── effective_pii_levels ──────────────────────────────────────────────────────


def test_effective_pii_levels_expands_global() -> None:
    from observe_kit.settings import get_observe_kit_settings

    cfg = get_observe_kit_settings()
    # pii_levels not set → global pii_level (BASIC) is broadcast to all sinks
    levels = cfg.effective_pii_levels
    assert all(v == "BASIC" for v in levels.values())
    assert set(levels) == {"logs", "otel", "sentry", "audit"}


def test_effective_pii_levels_uses_per_sink_when_set() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    per_sink = {"logs": "NONE", "otel": "SENSITIVE", "sentry": "BASIC", "audit": "NONE"}
    django_settings.OBSERVE_KIT = {"PII_LEVELS": per_sink}  # type: ignore[attr-defined]
    try:
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.effective_pii_levels == per_sink
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


# ── Master switch ─────────────────────────────────────────────────────────────


def test_enabled_false_bool() -> None:
    from django.conf import settings as django_settings

    original = getattr(django_settings, "OBSERVE_KIT", None)
    django_settings.OBSERVE_KIT = {"ENABLED": False}  # type: ignore[attr-defined]
    try:
        from observe_kit.settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()
        assert cfg.enabled is False
    finally:
        if original is None:
            del django_settings.OBSERVE_KIT  # type: ignore[attr-defined]
        else:
            django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]
