"""Comprehensive tests for Wagtail hooks."""

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


@pytest.fixture
def mock_page() -> Mock:
    """Create a mock page."""
    page = Mock()
    page.id = 123
    page.title = "Test Page"
    return page


def test_audit_publish_page(request_factory: RequestFactory, mock_page: Mock) -> None:
    """Test audit_publish_page hook."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.wagtail_integration.wagtail_hooks import audit_publish_page

    reset_request_context()
    context = RequestContext()
    context.tenant_id = "test-tenant"
    set_request_context(context)

    request = request_factory.get("/")
    request.user = Mock(id=1)

    with (
        patch("observe_kit.wagtail_integration.wagtail_hooks.audit") as mock_audit,
        patch("observe_kit.wagtail_integration.wagtail_hooks.WAGTAIL_PUBLISHED") as mock_metric,
        patch("observe_kit.wagtail_integration.wagtail_hooks.logger") as mock_logger,
    ):
        audit_publish_page(request, mock_page)

        mock_audit.assert_called_once()
        mock_metric.labels.assert_called_once_with("test-tenant")
        mock_metric.labels.return_value.inc.assert_called_once()
        mock_logger.info.assert_called_once()


def test_audit_unpublish_page(request_factory: RequestFactory, mock_page: Mock) -> None:
    """Test audit_unpublish_page hook."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.wagtail_integration.wagtail_hooks import audit_unpublish_page

    reset_request_context()
    context = RequestContext()
    context.tenant_id = "test-tenant"
    set_request_context(context)

    request = request_factory.get("/")
    request.user = Mock(id=1)

    with (
        patch("observe_kit.wagtail_integration.wagtail_hooks.audit") as mock_audit,
        patch("observe_kit.wagtail_integration.wagtail_hooks.WAGTAIL_UNPUBLISHED") as mock_metric,
        patch("observe_kit.wagtail_integration.wagtail_hooks.logger") as mock_logger,
    ):
        audit_unpublish_page(request, mock_page)

        mock_audit.assert_called_once()
        mock_metric.labels.assert_called_once_with("test-tenant")
        mock_metric.labels.return_value.inc.assert_called_once()
        mock_logger.info.assert_called_once()


def test_audit_delete_page(request_factory: RequestFactory, mock_page: Mock) -> None:
    """Test audit_delete_page hook."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.wagtail_integration.wagtail_hooks import audit_delete_page

    reset_request_context()
    context = RequestContext()
    context.tenant_id = "test-tenant"
    set_request_context(context)

    request = request_factory.get("/")
    request.user = Mock(id=1)

    with (
        patch("observe_kit.wagtail_integration.wagtail_hooks.audit") as mock_audit,
        patch("observe_kit.wagtail_integration.wagtail_hooks.WAGTAIL_DELETED") as mock_metric,
        patch("observe_kit.wagtail_integration.wagtail_hooks.logger") as mock_logger,
    ):
        audit_delete_page(request, mock_page)

        mock_audit.assert_called_once()
        mock_metric.labels.assert_called_once_with("test-tenant")
        mock_metric.labels.return_value.inc.assert_called_once()
        mock_logger.info.assert_called_once()


def test_audit_publish_page_no_tenant(request_factory: RequestFactory, mock_page: Mock) -> None:
    """Test audit_publish_page with no tenant in context."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.wagtail_integration.wagtail_hooks import audit_publish_page

    reset_request_context()
    context = RequestContext()
    context.tenant_id = None
    set_request_context(context)

    request = request_factory.get("/")
    request.user = Mock(id=1)

    with (
        patch("observe_kit.wagtail_integration.wagtail_hooks.audit"),
        patch("observe_kit.wagtail_integration.wagtail_hooks.WAGTAIL_PUBLISHED") as mock_metric,
    ):
        audit_publish_page(request, mock_page)

        mock_metric.labels.assert_called_once_with("unknown")


def test_audit_publish_page_no_user(request_factory: RequestFactory, mock_page: Mock) -> None:
    """Test audit_publish_page with no user in request."""
    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.wagtail_integration.wagtail_hooks import audit_publish_page

    reset_request_context()
    context = RequestContext()
    set_request_context(context)

    request = request_factory.get("/")
    # No user attribute

    with patch("observe_kit.wagtail_integration.wagtail_hooks.audit") as mock_audit:
        audit_publish_page(request, mock_page)

        call_args = mock_audit.call_args[1]
        assert call_args["actor"] is None
