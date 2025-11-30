"""Integration tests for audit admin.

These tests require Django admin to be fully configured with a complete Django setup.
Note: These tests use the django_client fixture which may wait for external services.
If services are not available, tests will be skipped.
"""

import pytest
from django.contrib import admin
from django.test import Client

pytestmark = pytest.mark.integration


def test_audit_admin_registered(django_client: Client) -> None:
    """Test that AuditLogAdmin is registered."""
    # Import admin to trigger registration
    from observe_kit.audit import admin as audit_admin  # noqa: F401
    from observe_kit.audit.models import AuditLog

    assert AuditLog in admin.site._registry
    admin_class = admin.site._registry[AuditLog]
    assert admin_class is not None


def test_audit_admin_list_display(django_client: Client) -> None:
    """Test that AuditLogAdmin has correct list_display."""
    from observe_kit.audit.models import AuditLog

    admin_class = admin.site._registry[AuditLog]
    assert "timestamp" in admin_class.list_display
    assert "actor" in admin_class.list_display
    assert "action" in admin_class.list_display
    assert "object_type" in admin_class.list_display
    assert "tenant_id" in admin_class.list_display
    assert "trace_id" in admin_class.list_display


def test_audit_admin_search_fields(django_client: Client) -> None:
    """Test that AuditLogAdmin has correct search_fields."""
    from observe_kit.audit.models import AuditLog

    admin_class = admin.site._registry[AuditLog]
    assert "action" in admin_class.search_fields
    assert "object_type" in admin_class.search_fields
    assert "tenant_id" in admin_class.search_fields
    assert "trace_id" in admin_class.search_fields


def test_audit_admin_list_filter(django_client: Client) -> None:
    """Test that AuditLogAdmin has correct list_filter."""
    from observe_kit.audit.models import AuditLog

    admin_class = admin.site._registry[AuditLog]
    assert "action" in admin_class.list_filter
