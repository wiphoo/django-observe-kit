from __future__ import annotations

from typing import Any

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "object_type", "tenant_id", "trace_id")
    search_fields = ("action", "object_type", "tenant_id", "trace_id", "actor__username")
    list_filter = ("action",)

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False
