"""Integration tests for tenant resolution with real Django requests."""

from typing import Optional

import pytest
from django.test import Client, RequestFactory

pytestmark = pytest.mark.integration

from observe_kit.tenant import resolve_tenant_id  # noqa: E402


@pytest.fixture
def request_factory(django_client: Client) -> RequestFactory:
    """Django request factory."""
    return RequestFactory()


def test_resolve_tenant_from_header(request_factory: RequestFactory) -> None:
    """Test tenant resolution from X-Tenant-ID header."""
    request = request_factory.get("/test/", HTTP_X_TENANT_ID="tenant-123")
    request.tenant = None
    request.get_host = lambda: "example.com"

    tenant_id = resolve_tenant_id(request)
    assert tenant_id == "tenant-123"


def test_resolve_tenant_from_subdomain(request_factory: RequestFactory) -> None:
    """Test tenant resolution from subdomain."""
    request = request_factory.get("/test/")
    request.tenant = None
    request.META = {}
    request.get_host = lambda: "tenant1.example.com"

    tenant_id = resolve_tenant_id(request)
    assert tenant_id == "tenant1"


@pytest.mark.parametrize(
    "host,expected",
    [
        ("tenant1.example.com", "tenant1"),
        ("tenant2.example.com", "tenant2"),
        ("www.example.com", None),
        ("localhost", None),
        ("example.com", None),
    ],
)
def test_resolve_tenant_various_hosts(
    request_factory: RequestFactory, host: str, expected: Optional[str]
) -> None:
    """Test tenant resolution with various host values."""
    request = request_factory.get("/test/")
    request.tenant = None
    request.META = {}
    request.get_host = lambda: host

    tenant_id = resolve_tenant_id(request)
    assert tenant_id == expected


def test_resolve_tenant_priority_tenant_object(request_factory: RequestFactory) -> None:
    """Test that tenant object takes priority over header/subdomain."""

    class MockTenant:
        id = "tenant-from-object"

    request = request_factory.get("/test/", HTTP_X_TENANT_ID="tenant-from-header")
    request.tenant = MockTenant()
    request.get_host = lambda: "tenant-from-subdomain.example.com"

    tenant_id = resolve_tenant_id(request)
    assert tenant_id == "tenant-from-object"


def test_resolve_tenant_priority_header_over_subdomain(request_factory: RequestFactory) -> None:
    """Test that header takes priority over subdomain."""
    request = request_factory.get("/test/", HTTP_X_TENANT_ID="tenant-from-header")
    request.tenant = None
    request.get_host = lambda: "tenant-from-subdomain.example.com"

    tenant_id = resolve_tenant_id(request)
    assert tenant_id == "tenant-from-header"
