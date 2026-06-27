"""Integration tests for audit logging with real database."""

from typing import TYPE_CHECKING, Generator

import pytest
from django.test import Client

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from django.contrib.auth.models import User


@pytest.fixture
def test_user(django_client: Client) -> Generator["User", None, None]:
    """Set up database for audit tests."""
    from django.contrib.auth.models import User  # noqa: F401

    # Create test user
    user = User.objects.create_user(username="testuser", email="test@example.com")
    yield user
    User.objects.all().delete()


def test_audit_creates_database_entry(test_user: "User", django_client: Client) -> None:
    """Test that audit() creates actual database entry."""
    from observe_kit.audit.models import AuditLog  # noqa: F401
    from observe_kit.audit.utils import audit  # noqa: F401

    user = test_user

    # Create audit entry
    audit_entry = audit(actor=user, action="test_action", obj=None)

    assert audit_entry is not None
    assert audit_entry.id is not None
    assert audit_entry.actor_id == user.id  # actor_id is an integer ForeignKey
    assert audit_entry.action == "test_action"

    # Verify it's in database
    assert AuditLog.objects.filter(id=audit_entry.id).exists()


def test_audit_includes_trace_id(test_user: "User", django_client: Client) -> None:
    """Test that audit entries include trace_id from context."""
    from observe_kit.audit.models import AuditLog  # noqa: F401
    from observe_kit.audit.utils import audit  # noqa: F401
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    context.trace_id = "test-trace-123"
    set_request_context(context)

    user = test_user
    audit_entry = audit(actor=user, action="test")

    assert audit_entry.trace_id == "test-trace-123"

    # Verify in database
    db_entry = AuditLog.objects.get(id=audit_entry.id)
    assert db_entry.trace_id == "test-trace-123"


def test_audit_includes_tenant_id(test_user: "User", django_client: Client) -> None:
    """Test that audit entries include tenant_id from context."""
    from observe_kit.audit.models import AuditLog  # noqa: F401
    from observe_kit.audit.utils import audit  # noqa: F401
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    context.tenant_id = "test-tenant-456"
    set_request_context(context)

    user = test_user
    audit_entry = audit(actor=user, action="test")

    assert audit_entry.tenant_id == "test-tenant-456"

    # Verify in database
    db_entry = AuditLog.objects.get(id=audit_entry.id)
    assert db_entry.tenant_id == "test-tenant-456"


@pytest.mark.parametrize(
    "action,object_type", [("create", "User"), ("update", "Post"), ("delete", "Comment")]
)
def test_audit_various_actions(
    test_user: "User", django_client: Client, action: str, object_type: str
) -> None:
    """Test audit with various actions creates correct database entries."""
    from observe_kit.audit.models import AuditLog  # noqa: F401
    from observe_kit.audit.utils import audit  # noqa: F401

    user = test_user

    # Create a mock object
    class MockObj:
        def __init__(self, obj_type):
            self.__class__.__name__ = obj_type
            self.pk = 123

    obj = MockObj(object_type) if object_type else None

    audit_entry = audit(actor=user, action=action, obj=obj)

    assert audit_entry.action == action
    if object_type:
        assert audit_entry.object_type == object_type
        assert audit_entry.object_id == "123"

    # Verify in database
    db_entry = AuditLog.objects.get(id=audit_entry.id)
    assert db_entry.action == action


def test_audit_with_extra_data(test_user: "User", django_client: Client) -> None:
    """Test that audit entries store extra data."""
    from observe_kit.audit.models import AuditLog  # noqa: F401
    from observe_kit.audit.utils import audit  # noqa: F401

    user = test_user
    extra = {"field1": "value1", "field2": 123}

    audit_entry = audit(actor=user, action="test", extra=extra)

    assert audit_entry.extra == extra

    # Verify in database
    db_entry = AuditLog.objects.get(id=audit_entry.id)
    assert db_entry.extra == extra


def test_audit_log_model_ordering(test_user: "User", django_client: Client) -> None:
    """Test that AuditLog model has correct ordering."""
    import time

    from observe_kit.audit.models import AuditLog  # noqa: F401
    from observe_kit.audit.utils import audit  # noqa: F401

    user = test_user

    # Create multiple entries with minimal delay
    audit(actor=user, action="action1")
    time.sleep(0.001)  # Minimal delay to ensure different timestamps
    audit(actor=user, action="action2")
    time.sleep(0.001)
    audit(actor=user, action="action3")

    # Query should return in reverse chronological order
    entries = list(AuditLog.objects.all()[:3])
    assert len(entries) >= 2

    # Most recent should be first
    if len(entries) >= 2:
        assert entries[0].timestamp >= entries[1].timestamp


def test_audit_with_request_metadata(test_user: "User", django_client: Client) -> None:
    """Test that audit entries extract metadata from request object."""
    from django.test import RequestFactory

    from observe_kit.audit.models import AuditLog
    from observe_kit.audit.utils import audit
    from observe_kit.context import reset_request_context

    # Reset context to ensure no tenant_id from previous tests
    reset_request_context()

    request_factory = RequestFactory()
    request = request_factory.get(
        "/test?param=value", HTTP_USER_AGENT="test-agent", REMOTE_ADDR="192.168.1.1"
    )

    # Create a mock tenant object
    class MockTenant:
        id = "tenant-123"

    request.tenant = MockTenant()

    user = test_user
    audit_entry = audit(actor=user, action="test_with_request", request=request)

    assert audit_entry.tenant_id == "tenant-123"
    assert audit_entry.remote_addr == "192.168.1.1"
    assert audit_entry.user_agent == "test-agent"

    # Verify in database
    db_entry = AuditLog.objects.get(id=audit_entry.id)
    assert db_entry.tenant_id == "tenant-123"
    assert db_entry.remote_addr == "192.168.1.1"
    assert db_entry.user_agent == "test-agent"
