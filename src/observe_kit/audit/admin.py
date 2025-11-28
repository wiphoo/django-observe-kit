from __future__ import annotations

from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "object_type", "tenant_id")
    search_fields = ("action", "object_type", "tenant_id")
    list_filter = ("action",)
