from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


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
