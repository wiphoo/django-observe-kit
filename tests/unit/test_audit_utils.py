"""Unit tests for audit utils."""

from unittest.mock import Mock, patch

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


@pytest.fixture
def request_factory() -> RequestFactory:
    """Create a request factory."""
    from django.test import RequestFactory

    return RequestFactory()


def test_audit_with_obj_pk(request_factory: RequestFactory) -> None:
    """Test audit with object that has pk attribute."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    class TestModel:
        pk = 123

    obj = TestModel()
    request = request_factory.get("/")

    with patch("observe_kit.audit.utils.AuditLog") as mock_audit_log:
        mock_entry = Mock()
        mock_entry.id = 1
        mock_entry.object_type = "TestModel"
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test", obj=obj, request=request)

        call_args = mock_audit_log.objects.create.call_args[1]
        assert call_args["object_id"] == "123"
        assert call_args["object_type"] == "TestModel"


def test_audit_with_obj_id(request_factory: RequestFactory) -> None:
    """Test audit with object that has id attribute."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    class TestModel:
        id = 456

    obj = TestModel()
    request = request_factory.get("/")

    with patch("observe_kit.audit.utils.AuditLog") as mock_audit_log:
        mock_entry = Mock()
        mock_entry.id = 1
        mock_entry.object_type = "TestModel"
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test", obj=obj, request=request)

        call_args = mock_audit_log.objects.create.call_args[1]
        assert call_args["object_id"] == "456"


def test_audit_with_obj_no_id(request_factory: RequestFactory) -> None:
    """Test audit with object that has no id or pk."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    class TestModel:
        pass

    obj = TestModel()
    request = request_factory.get("/")

    with patch("observe_kit.audit.utils.AuditLog") as mock_audit_log:
        mock_entry = Mock()
        mock_entry.id = 1
        mock_entry.object_type = "TestModel"
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test", obj=obj, request=request)

        call_args = mock_audit_log.objects.create.call_args[1]
        assert call_args["object_id"] is None


def test_audit_with_tenant_from_request(request_factory: RequestFactory) -> None:
    """Test audit extracts tenant from request.tenant."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    class MockTenant:
        id = "tenant-from-request"

    request = request_factory.get("/")
    request.tenant = MockTenant()

    with patch("observe_kit.audit.utils.AuditLog") as mock_audit_log:
        mock_entry = Mock()
        mock_entry.id = 1
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test", request=request)

        call_args = mock_audit_log.objects.create.call_args[1]
        assert call_args["tenant_id"] == "tenant-from-request"


def test_audit_with_remote_addr_from_request(request_factory: RequestFactory) -> None:
    """Test audit extracts remote_addr from request.META."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    request = request_factory.get("/", REMOTE_ADDR="192.168.1.100")

    with patch("observe_kit.audit.utils.AuditLog") as mock_audit_log:
        mock_entry = Mock()
        mock_entry.id = 1
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test", request=request)

        call_args = mock_audit_log.objects.create.call_args[1]
        assert call_args["remote_addr"] == "192.168.1.100"


def test_audit_with_user_agent_from_request(request_factory: RequestFactory) -> None:
    """Test audit extracts user_agent from request.META."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    request = request_factory.get("/", HTTP_USER_AGENT="test-agent/1.0")

    with patch("observe_kit.audit.utils.AuditLog") as mock_audit_log:
        mock_entry = Mock()
        mock_entry.id = 1
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test", request=request)

        call_args = mock_audit_log.objects.create.call_args[1]
        assert call_args["user_agent"] == "test-agent/1.0"


def test_audit_logs_event(request_factory: RequestFactory) -> None:
    """Test that audit logs the event."""
    from observe_kit.audit.utils import audit
    from observe_kit.context import RequestContext, reset_request_context, set_request_context

    reset_request_context()
    context = RequestContext()
    context.trace_id = "test-trace-123"
    set_request_context(context)

    request = request_factory.get("/")

    with (
        patch("observe_kit.audit.utils.AuditLog") as mock_audit_log,
        patch("observe_kit.audit.utils.logger") as mock_logger,
    ):
        mock_entry = Mock()
        mock_entry.id = 1
        mock_entry.object_type = None
        mock_audit_log.objects.create.return_value = mock_entry

        audit(actor=None, action="test_action", request=request)

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        assert call_args[0][0] == "audit_event"
        assert call_args[1]["extra"]["action"] == "test_action"
        assert call_args[1]["extra"]["trace_id"] == "test-trace-123"
