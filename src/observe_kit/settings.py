"""ObserveKitSettings: load library configuration from Django settings + env vars.

Priority for each key: OBSERVE_KIT dict in Django settings → environment variable → default.

Minimal setup::

    # settings.py
    INSTALLED_APPS = [..., "observe_kit"]

    OBSERVE_KIT = {
        "SERVICE_NAME": "my-app",
        "OTEL_ENDPOINT": "http://localhost:4318",
    }

All keys are optional. OTEL tracing is only initialised when SERVICE_NAME is set.
Sentry is only initialised when SENTRY_DSN is set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, cast


@dataclass
class ObserveKitSettings:
    """Resolved configuration for the observe_kit library."""

    configured: bool
    """True when the OBSERVE_KIT Django setting is present and valid."""

    service_name: Optional[str]
    """OTEL service name. Tracing is skipped when None."""

    otel_endpoint: Optional[str]
    """Base OTLP HTTP endpoint, e.g. ``http://localhost:4318``.
    Exporters append ``/v1/traces`` and ``/v1/logs`` automatically.
    Falls back to OpenTelemetry SDK default (localhost:4318) when None.
    """

    log_level: str
    """Python logging level (DEBUG / INFO / WARNING / ERROR / CRITICAL)."""

    pii_level: str
    """Global PII sanitisation level applied to all sinks when pii_levels is None."""

    pii_levels: Optional[Dict[str, str]]
    """Per-sink PII levels (logs / otel / sentry / audit). Overrides pii_level."""

    sentry_dsn: Optional[str]
    """Sentry DSN. Sentry integration is skipped when None."""

    sentry_environment: str
    """Sentry environment tag, e.g. ``production`` or ``staging``."""

    sentry_traces_sample_rate: float
    """Sentry performance tracing sample rate (0.0–1.0)."""

    enabled: bool
    """Master switch. When False, AppConfig.ready() is a no-op."""

    db_tracking: bool
    """Enable per-request DB query tracking (slight performance overhead)."""

    pii_hash_salt: str
    """Salt prepended before hashing PII values (e.g. IP, user-agent).
    Set to a secret per-environment value to prevent rainbow-table reversal.
    """

    extra_drop_headers: FrozenSet[str]
    """Additional header names (lowercase) to drop beyond the built-in set."""

    extra_mask_fields: FrozenSet[str]
    """Additional field names (lowercase) to mask beyond the built-in set."""

    extra_hash_fields: FrozenSet[str]
    """Additional field names (lowercase) to hash beyond the built-in set."""

    trusted_proxies: List[str]
    """List of trusted proxy IPs (or ``["*"]`` for any proxy).
    When non-empty, ``X-Forwarded-For`` is used to resolve the client IP.
    """

    otel_sample_rate: Optional[float]
    """Trace sampling ratio (0.0–1.0). None means 100% sampling (ALWAYS_ON)."""

    @property
    def effective_pii_levels(self) -> Dict[str, str]:
        """Per-sink PII levels, expanding the global level when pii_levels is None."""
        if self.pii_levels:
            return self.pii_levels
        return {
            "logs": self.pii_level,
            "otel": self.pii_level,
            "sentry": self.pii_level,
            "audit": self.pii_level,
        }


def _as_frozenset_lower(raw: object) -> FrozenSet[str]:
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(str(item).lower() for item in raw)
    return frozenset()


def get_observe_kit_settings() -> ObserveKitSettings:
    """Load :class:`ObserveKitSettings` from Django settings and environment variables.

    Reading precedence for each key:
    1. ``OBSERVE_KIT`` dict in ``django.conf.settings`` (if key is present)
    2. The corresponding environment variable (if set)
    3. Hardcoded default

    Safe to call before Django is fully initialised — returns all defaults if
    ``django.conf.settings`` is not yet configured or ``OBSERVE_KIT`` is absent.
    """
    user_config: dict[str, object] = {}
    try:
        from django.conf import settings as django_settings

        raw_user_config = getattr(django_settings, "OBSERVE_KIT", None)
        configured = isinstance(raw_user_config, dict)
        user_config = cast(dict[str, object], raw_user_config) if configured else {}
    except Exception:
        configured = False

    def _get(key: str, env_var: Optional[str] = None, default: object = None) -> object:
        if key in user_config:
            return user_config[key]
        if env_var:
            val = os.environ.get(env_var)
            if val is not None:
                return val
        return default

    raw_sample_rate = _get("SENTRY_TRACES_SAMPLE_RATE", default=0.0)
    try:
        sample_rate = float(raw_sample_rate)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        sample_rate = 0.0

    raw_enabled = _get("ENABLED", default=True)
    enabled = _as_bool(raw_enabled)

    raw_db_tracking = _get("DB_TRACKING", default=True)
    db_tracking = _as_bool(raw_db_tracking)

    pii_levels = _get("PII_LEVELS", default=None)

    raw_otel_sample_rate = _get("OTEL_SAMPLE_RATE", default=None)
    otel_sample_rate: Optional[float]
    if raw_otel_sample_rate is not None:
        try:
            parsed = float(raw_otel_sample_rate)  # type: ignore[arg-type]
            otel_sample_rate = max(0.0, min(1.0, parsed))
        except (TypeError, ValueError):
            otel_sample_rate = None
    else:
        otel_sample_rate = None

    raw_trusted = _get("TRUSTED_PROXIES", default=None)
    trusted_proxies: List[str] = list(raw_trusted) if isinstance(raw_trusted, (list, tuple)) else []

    extra_drop_headers = _as_frozenset_lower(_get("EXTRA_DROP_HEADERS", default=None))
    extra_mask_fields = _as_frozenset_lower(_get("EXTRA_MASK_FIELDS", default=None))
    extra_hash_fields = _as_frozenset_lower(_get("EXTRA_HASH_FIELDS", default=None))

    return ObserveKitSettings(
        configured=configured,
        service_name=_get("SERVICE_NAME", "OTEL_SERVICE_NAME") or None,  # type: ignore[arg-type]
        otel_endpoint=_get("OTEL_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT") or None,  # type: ignore[arg-type]
        log_level=str(_get("LOG_LEVEL", "LOG_LEVEL", "INFO")).upper(),
        pii_level=str(_get("PII_LEVEL", default="BASIC")).upper(),
        pii_levels=pii_levels if isinstance(pii_levels, dict) else None,
        sentry_dsn=_get("SENTRY_DSN", "SENTRY_DSN") or None,  # type: ignore[arg-type]
        sentry_environment=str(_get("SENTRY_ENVIRONMENT", "SENTRY_ENVIRONMENT", "production")),
        sentry_traces_sample_rate=sample_rate,
        enabled=enabled,
        db_tracking=db_tracking,
        pii_hash_salt=str(_get("PII_HASH_SALT", default="")),
        extra_drop_headers=extra_drop_headers,
        extra_mask_fields=extra_mask_fields,
        extra_hash_fields=extra_hash_fields,
        trusted_proxies=trusted_proxies,
        otel_sample_rate=otel_sample_rate,
    )


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() != "false"
