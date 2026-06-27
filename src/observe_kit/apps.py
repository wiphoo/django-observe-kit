from __future__ import annotations

import logging
from typing import Sequence

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Canonical observe_kit middleware order. Reported order for missing-required
# warnings; matches docs/HYPERDX_QUICKSTART.md and examples/*/settings.py.
_OTEL = "observe_kit.otel.middleware.TraceContextMiddleware"
_LOGGING = "observe_kit.logging.middleware.RequestLoggingMiddleware"
_METRICS = "observe_kit.metrics.middleware.PrometheusRequestMiddleware"
_CONTEXT = "observe_kit.context_middleware.RequestContextMiddleware"
_USER = "observe_kit.context_middleware.UserLoggingContextMiddleware"
_DRF = "observe_kit.drf.integration.DRFIntegrationMiddleware"
_SENTRY = "observe_kit.sentry.middleware.SentryContextMiddleware"

_CANONICAL_MIDDLEWARE_ORDER: tuple[tuple[str, bool], ...] = (
    (_OTEL, True),
    (_LOGGING, True),
    (_METRICS, True),
    (_CONTEXT, True),
    (_USER, True),
    (_DRF, False),
    (_SENTRY, True),
)

# Behaviorally-significant ordering constraints expressed as (above, below) pairs
# in MIDDLEWARE list order. We *only* flag inversions of these pairs — pairs not
# listed (e.g. DRF↔Sentry, where DRF only does ``process_view`` and Sentry only
# ``process_request`` so their order is irrelevant) never produce warnings.
_MIDDLEWARE_ORDER_EDGES: tuple[tuple[str, str], ...] = (
    (_OTEL, _LOGGING),  # trace_id must be set before logging emits the record
    (_OTEL, _METRICS),  # trace_id must be set before metrics record the request
    # process_response runs bottom-up: CONTEXT must be BELOW logging/metrics so
    # its finalisation runs first, populating duration_ms/db_queries/status.
    (_LOGGING, _CONTEXT),
    (_METRICS, _CONTEXT),
    (_CONTEXT, _USER),  # USER re-binds the contextvar populated by CONTEXT
    (_OTEL, _SENTRY),  # Sentry scope needs trace context
    (_USER, _SENTRY),  # Sentry scope needs user_id
)


def _validate_middleware_order(middleware: Sequence[str]) -> list[str]:
    """Return human-readable warnings for misordered observe_kit middleware.

    Pure function — no Django imports, no logging. Empty list means the
    observe_kit middlewares present in ``middleware`` satisfy every
    declared ordering edge and no required entries are missing.

    A user with no observe_kit middleware at all (programmatic usage) gets
    no warnings.
    """
    found: dict[str, int] = {}
    for idx, entry in enumerate(middleware):
        # Record the first occurrence if duplicates exist.
        if entry not in found:
            found[entry] = idx

    # Filter to observe_kit middlewares known to the canonical order.
    known = {path for path, _ in _CANONICAL_MIDDLEWARE_ORDER}
    present_with_idx = {path: idx for path, idx in found.items() if path in known}

    if not present_with_idx:
        return []

    warnings: list[str] = []

    # Edge check: only flag declared behaviorally-significant inversions.
    for above, below in _MIDDLEWARE_ORDER_EDGES:
        if above in present_with_idx and below in present_with_idx:
            if present_with_idx[above] > present_with_idx[below]:
                warnings.append(
                    f"observe_kit: MIDDLEWARE order — '{above}' "
                    f"(index {present_with_idx[above]}) should appear before "
                    f"'{below}' (index {present_with_idx[below]})."
                )

    # Iterate canonical order (a tuple, deterministic) for missing-required
    # warnings so log output is stable across runs.
    for path, required in _CANONICAL_MIDDLEWARE_ORDER:
        if required and path not in present_with_idx:
            warnings.append(f"observe_kit: MIDDLEWARE is missing required entry '{path}'.")

    return warnings


class ObserveKitConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "observe_kit"
    verbose_name = "Observe Kit"

    def ready(self) -> None:
        """Auto-initialise observe_kit from the OBSERVE_KIT Django setting.

        Called once after all apps are loaded. Reads
        ``django.conf.settings.OBSERVE_KIT`` (optional dict) and initialises:

        - JSON structured logging (always, when ENABLED is True)
        - OpenTelemetry tracing + log export (when SERVICE_NAME is set)
        - Sentry SDK (when SENTRY_DSN is set)

        Users who prefer manual control can skip ``OBSERVE_KIT`` and call
        ``configure_logging()``, ``init_tracing()``, and ``init_sentry()``
        themselves — this method is a no-op when ``OBSERVE_KIT`` is absent.
        """
        from .settings import get_observe_kit_settings

        cfg = get_observe_kit_settings()

        if not cfg.configured or not cfg.enabled:
            return

        # Warn about insecure configuration choices early so they surface in startup logs.
        if not cfg.pii_hash_salt:
            logger.warning(
                "observe_kit: PII_HASH_SALT is not set. "
                "Hashed PII values (IP, user-agent) are vulnerable to rainbow-table reversal. "
                "Set OBSERVE_KIT['PII_HASH_SALT'] to a secret per-environment value."
            )
        if cfg.trusted_proxies == ["*"]:
            logger.warning(
                "observe_kit: TRUSTED_PROXIES is set to ['*'], which trusts any proxy for "
                "X-Forwarded-For. This can allow clients to spoof their IP address. "
                "Consider restricting to explicit proxy IPs in production."
            )

        # Validate MIDDLEWARE order (advisory; never raises).
        if cfg.validate_middleware_order:
            try:
                from django.conf import settings as django_settings

                middleware = list(getattr(django_settings, "MIDDLEWARE", []) or [])
                for warning in _validate_middleware_order(middleware):
                    logger.warning(warning)
            except Exception:
                logger.exception("observe_kit: middleware-order validator failed")

        # Always configure structured JSON logging.
        try:
            from .logging import configure_logging

            configure_logging(level=cfg.log_level, pii_levels=cfg.effective_pii_levels)
        except Exception:
            logger.exception("observe_kit: failed to configure structured logging")

        # Initialise OTEL tracing (and OTEL log export) when a service name is given.
        if cfg.service_name:
            try:
                from .otel import init_tracing

                init_tracing(
                    service_name=cfg.service_name,
                    endpoint=cfg.otel_endpoint,
                    sample_rate=cfg.otel_sample_rate,
                )
            except Exception:
                logger.exception(
                    "observe_kit: failed to initialise OTel tracing",
                    extra={"service": cfg.service_name},
                )

        # Initialise Sentry when a DSN is provided.
        if cfg.sentry_dsn:
            try:
                from .sentry import init_sentry

                init_sentry(
                    dsn=cfg.sentry_dsn,
                    environment=cfg.sentry_environment,
                    traces_sample_rate=cfg.sentry_traces_sample_rate,
                )
            except Exception:
                logger.exception("observe_kit: failed to initialise Sentry")
