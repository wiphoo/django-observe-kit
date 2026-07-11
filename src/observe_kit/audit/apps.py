from __future__ import annotations

from django.apps import AppConfig


class ObserveAuditConfig(AppConfig):
    name = "observe_kit.audit"
    verbose_name = "Observe Kit Audit"
    # Pin the implicit primary-key type so it always matches the shipped
    # 0001_initial migration (which creates AuditLog.id as AutoField),
    # regardless of the host project's DEFAULT_AUTO_FIELD (e.g. BigAutoField).
    # Without this, such projects would see spurious makemigrations drift.
    default_auto_field = "django.db.models.AutoField"

    def ready(self) -> None:
        from . import signals  # noqa: F401 — register signal handlers
