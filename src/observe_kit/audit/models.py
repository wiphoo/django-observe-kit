from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class AuditLogImmutableError(Exception):
    """Raised when code attempts to modify or delete an AuditLog record.

    Enforced at the ORM layer (``Model.save/delete``, ``QuerySet.delete/update``).
    Direct SQL access or DBA-level operations still bypass these guards — the
    library cannot prevent that, so production deployments should additionally
    revoke ``DELETE``/``UPDATE`` privileges on the audit table at the DB level.
    """


class AuditLogQuerySet(models.QuerySet):
    """QuerySet that refuses bulk delete/update of AuditLog rows.

    Without this guard, ``AuditLog.objects.filter(...).delete()`` would issue
    a single ``DELETE`` SQL statement that bypasses ``Model.delete()``; the
    same applies to ``update()``. Both are blocked here so the immutability
    contract holds at the ORM layer.
    """

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AuditLogImmutableError(
            "AuditLog rows are immutable; QuerySet.delete() is not permitted."
        )

    def update(self, **kwargs: Any) -> int:
        raise AuditLogImmutableError(
            "AuditLog rows are immutable; QuerySet.update() is not permitted."
        )


# Build the manager from the queryset so chained calls (e.g.
# ``AuditLog.objects.filter(...).delete()``) also raise.
AuditLogManager = models.Manager.from_queryset(AuditLogQuerySet)


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

    objects = AuditLogManager()

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:  # pragma: no cover - representation helper
        return f"{self.timestamp} {self.actor} {self.action}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise AuditLogImmutableError("AuditLog records are immutable and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise AuditLogImmutableError("AuditLog records are immutable and cannot be deleted.")
