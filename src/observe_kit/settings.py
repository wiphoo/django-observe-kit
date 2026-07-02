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
import warnings
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

    metrics_auth: str
    """Access-control mode for the Prometheus ``/metrics`` endpoint.

    One of ``"none"`` (allow all, warns when ``DEBUG`` is False), ``"staff"``
    (require ``request.user.is_staff``), or ``"token"`` (require an
    ``Authorization: Bearer <token>`` header matching :attr:`metrics_token`).
    Defaults to ``"none"`` for backwards compatibility; invalid values are
    coerced to ``"none"``.
    """

    metrics_token: Optional[str]
    """Bearer token required when :attr:`metrics_auth` is ``"token"``.

    Compared via :func:`hmac.compare_digest`. An empty or missing token
    rejects every request, even when the client sends an empty header.
    """

    trust_incoming_trace_context: bool
    """Master switch for honouring inbound W3C ``traceparent`` / ``tracestate``.

    Defaults to ``False`` because most Django apps run at the network edge,
    where any client can forge trace headers and poison trace storage. When
    ``False``, every inbound trace context is dropped and a fresh root span
    is started — :attr:`trusted_trace_sources` is ignored. Set to ``True``
    for mesh-internal services that need end-to-end trace propagation, then
    optionally restrict to specific IPs via :attr:`trusted_trace_sources`.
    """

    trusted_trace_sources: List[str]
    """Optional IP / CIDR allow-list that restricts trust when
    :attr:`trust_incoming_trace_context` is ``True``.

    Empty list (default) means "trust every inbound trace when the global
    flag is on". A non-empty list narrows trust to requests whose resolved
    client IP (proxy-aware via :attr:`trusted_proxies`) matches one of the
    entries. Ignored entirely when :attr:`trust_incoming_trace_context` is
    ``False`` — the global flag is a hard off-switch. IPv4 and IPv6
    addresses or CIDR blocks are accepted; malformed entries are ignored.
    """

    validate_middleware_order: bool
    """Warn at startup when ``django.conf.settings.MIDDLEWARE`` contains
    observe_kit middlewares in an order that will cause silent data loss
    (e.g. trace_id missing from logs, duration_ms missing from metrics).
    Advisory only; never raises.
    """

    metrics_max_label_cardinality: int
    """Per-process cap on distinct values for the ``route`` and ``tenant``
    Prometheus labels. Values beyond the cap collapse to a reserved sentinel
    to prevent attacker-controlled inputs (raw paths, ``X-Tenant-Id``
    headers, subdomains) from inflating the time-series count. Set to ``0``
    to disable the cap. Default ``1000``.
    """

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

    raw_trust_trace = _get(
        "TRUST_INCOMING_TRACE_CONTEXT", "OBSERVE_KIT_TRUST_INCOMING_TRACE_CONTEXT", default=False
    )
    # Strict bool parsing: only canonical truthy strings enable trust. Refuses
    # to interpret "0", "no", "off", "" as truthy — security-sensitive flag.
    trust_incoming_trace_context = _as_strict_bool(raw_trust_trace, default=False)

    raw_trace_sources = _get(
        "TRUSTED_TRACE_SOURCES", "OBSERVE_KIT_TRUSTED_TRACE_SOURCES", default=None
    )
    if isinstance(raw_trace_sources, (list, tuple)):
        trusted_trace_sources = [str(x) for x in raw_trace_sources]
    elif isinstance(raw_trace_sources, str) and raw_trace_sources:
        trusted_trace_sources = [s.strip() for s in raw_trace_sources.split(",") if s.strip()]
    else:
        trusted_trace_sources = []

    raw_validate_order = _get(
        "VALIDATE_MIDDLEWARE_ORDER", "OBSERVE_KIT_VALIDATE_MIDDLEWARE_ORDER", default=True
    )
    # Strict bool: recognise "0"/"no"/"off" as False, not True. _as_bool treats
    # everything except "false" as True, which silently ignores common env-var
    # conventions; default stays True so unrecognised input doesn't disable the
    # validator unexpectedly.
    validate_middleware_order = _as_strict_bool(raw_validate_order, default=True)

    raw_max_cardinality = _get(
        "METRICS_MAX_LABEL_CARDINALITY", "OBSERVE_KIT_METRICS_MAX_LABEL_CARDINALITY", default=1000
    )
    # _get returns `object`; int() accepts str / SupportsInt at runtime.
    try:
        parsed_cap: int = int(raw_max_cardinality)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        metrics_max_label_cardinality = 1000
    else:
        metrics_max_label_cardinality = max(0, parsed_cap)

    extra_drop_headers = _as_frozenset_lower(_get("EXTRA_DROP_HEADERS", default=None))
    extra_mask_fields = _as_frozenset_lower(_get("EXTRA_MASK_FIELDS", default=None))
    extra_hash_fields = _as_frozenset_lower(_get("EXTRA_HASH_FIELDS", default=None))

    raw_metrics_auth = _get("METRICS_AUTH", "OBSERVE_KIT_METRICS_AUTH", "none")
    metrics_auth_input = str(raw_metrics_auth).lower() if raw_metrics_auth is not None else "none"
    if metrics_auth_input in {"none", "staff", "token"}:
        metrics_auth = metrics_auth_input
    else:
        # Surface misconfiguration immediately rather than waiting for the
        # one-shot /metrics warning in `metrics/prometheus.py`. Python dedupes
        # warnings by (message, category, file, line) so this fires once per
        # distinct invalid value.
        warnings.warn(
            f"observe_kit: OBSERVE_KIT['METRICS_AUTH']={raw_metrics_auth!r} is "
            "invalid (expected one of 'none', 'staff', 'token'); falling back "
            "to 'none' — /metrics will be exposed without authentication.",
            RuntimeWarning,
            stacklevel=2,
        )
        metrics_auth = "none"

    raw_metrics_token = _get("METRICS_TOKEN", "OBSERVE_KIT_METRICS_TOKEN", None)
    metrics_token = str(raw_metrics_token) if raw_metrics_token else None

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
        metrics_auth=metrics_auth,
        metrics_token=metrics_token,
        trust_incoming_trace_context=trust_incoming_trace_context,
        trusted_trace_sources=trusted_trace_sources,
        validate_middleware_order=validate_middleware_order,
        metrics_max_label_cardinality=metrics_max_label_cardinality,
    )


def env_flag(name: str, default: str = "1") -> bool:
    """Parse a boolean environment variable.

    An explicitly-empty value (e.g. ``FLAG=``) reads as ``False``.
    """
    return os.getenv(name, default).strip().lower() not in {"", "0", "false", "no"}


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() != "false"


_STRICT_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on", "t"})
_STRICT_FALSE_VALUES = frozenset({"false", "0", "no", "n", "off", "f", ""})


def _as_strict_bool(value: object, default: bool) -> bool:
    """Parse a config value to bool using explicit truthy/falsy strings.

    Unlike :func:`_as_bool` (kept for legacy callers, treats any non-"false"
    string as ``True``), this function recognises only canonical truthy and
    falsy strings and returns ``default`` for anything else. Use for flags
    where ambiguity must fail safe / fail correct.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _STRICT_TRUE_VALUES:
        return True
    if text in _STRICT_FALSE_VALUES:
        return False
    return default
