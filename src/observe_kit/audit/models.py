from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=128)
    object_type = models.CharField(max_length=128, null=True, blank=True)
    object_id = models.CharField(max_length=256, null=True, blank=True)
    tenant_id = models.CharField(max_length=128, null=True, blank=True)
    trace_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    remote_addr = models.CharField(max_length=64, null=True, blank=True)
    user_agent = models.CharField(max_length=256, null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:  # pragma: no cover - representation helper
        return f"{self.timestamp} {self.actor} {self.action}"


class ObserveAuditConfig(AppConfig):
    name = "observe_kit.audit"
    verbose_name = "Observe Kit Audit"
