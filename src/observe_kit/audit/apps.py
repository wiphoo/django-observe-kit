from __future__ import annotations

from django.apps import AppConfig


class ObserveAuditConfig(AppConfig):
    name = "observe_kit.audit"
    verbose_name = "Observe Kit Audit"

    def ready(self) -> None:
        from . import signals  # noqa: F401 — register signal handlers
