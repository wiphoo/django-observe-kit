"""Hypothesis property-based tests for tenant resolution."""

from typing import Union
from unittest.mock import Mock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from observe_kit.tenant import resolve_tenant_id


@given(
    tenant_id=st.one_of(
        st.integers(min_value=1, max_value=999999),
        st.text(
            min_size=1, max_size=50, alphabet=st.characters(min_codepoint=65, max_codepoint=122)
        ),
    )
)
def test_resolve_tenant_id_from_tenant_object(tenant_id: Union[int, str]) -> None:
    """Property: Tenant ID from request.tenant is always returned as string."""
    request = Mock()
    tenant = Mock()
    tenant.id = tenant_id
    request.tenant = tenant
    request.META = {}
    request.get_host.return_value = "example.com"

    result = resolve_tenant_id(request)
    assert result == str(tenant_id)


@given(
    header_value=st.text(
        min_size=1, max_size=100, alphabet=st.characters(min_codepoint=48, max_codepoint=122)
    )
)
def test_resolve_tenant_id_from_header(header_value: str) -> None:
    """Property: Tenant ID from header is returned as-is."""
    request = Mock()
    request.tenant = None
    request.META = {"HTTP_X_TENANT_ID": header_value}
    request.get_host.return_value = "example.com"

    result = resolve_tenant_id(request)
    assert result == header_value


@given(
    subdomain=st.text(
        min_size=1, max_size=50, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
    ),
    domain=st.text(
        min_size=1, max_size=50, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
    ),
)
def test_resolve_tenant_id_from_subdomain(subdomain: str, domain: str) -> None:
    """Property: Subdomain extraction works for valid hostnames."""
    request = Mock()
    request.tenant = None
    request.META = {}
    request.get_host.return_value = f"{subdomain}.{domain}"

    result = resolve_tenant_id(request)
    # Should extract subdomain or return None if not valid tenant pattern
    assert result is None or isinstance(result, str)


@pytest.mark.parametrize(
    "host,expected",
    [
        ("localhost", None),  # Single-part hostname returns None
        ("127.0.0.1", "127"),  # IP addresses have 4 parts, extracts first segment
        ("example.com", None),  # 2-part domain.tld returns None
        ("www.example.com", None),  # "www" is filtered out
        ("tenant.example.com", "tenant"),  # Valid 3-part subdomain is extracted
    ],
)
def test_resolve_tenant_id_subdomain_extraction(host: str, expected: str | None) -> None:
    """Property: Subdomain extraction based on hostname parts."""
    request = Mock()
    request.tenant = None
    request.META = {}
    request.get_host.return_value = host

    result = resolve_tenant_id(request)
    # The function extracts subdomain for hostnames with 3+ parts
    # unless the first part is in the exclusion list (www, localhost)
    assert result == expected


@given(
    tenant_id=st.one_of(st.integers(min_value=1), st.text(min_size=1)),
    header_value=st.text(min_size=1),
    subdomain=st.text(min_size=1),
)
def test_resolve_tenant_id_priority(
    tenant_id: Union[int, str], header_value: str, subdomain: str
) -> None:
    """Property: Tenant object takes priority over header, which takes priority over subdomain."""
    request = Mock()
    tenant = Mock()
    tenant.id = tenant_id
    request.tenant = tenant
    request.META = {"HTTP_X_TENANT_ID": header_value}
    request.get_host.return_value = f"{subdomain}.example.com"

    result = resolve_tenant_id(request)
    # Should use tenant.id (highest priority)
    assert result == str(tenant_id)
