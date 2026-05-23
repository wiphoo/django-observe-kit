"""Test that ``audit()`` resolves tenant via the canonical helper (#17)."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from observe_kit.audit.models import AuditLog
from observe_kit.audit.utils import audit
from observe_kit.context import RequestContext, reset_request_context, set_request_context


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset_ctx() -> None:
    reset_request_context()
    yield
    reset_request_context()


def test_audit_uses_request_context_tenant_when_available(rf: RequestFactory) -> None:
    """Happy path: context.tenant_id wins."""
    ctx = RequestContext()
    ctx.tenant_id = "ctx-tenant"
    set_request_context(ctx)

    request = rf.get("/x", HTTP_X_TENANT_ID="header-tenant")
    entry: AuditLog = audit(action="x", request=request)
    assert entry.tenant_id == "ctx-tenant"


def test_audit_falls_back_to_xtenantid_header_via_resolve_tenant_id(rf: RequestFactory) -> None:
    """When no request-scoped context exists, the audit path must honour the
    ``HTTP_X_TENANT_ID`` header (resolve_tenant_id) — previously this was
    missed because the audit path only inspected ``request.tenant.id``."""
    request = rf.get("/x", HTTP_X_TENANT_ID="header-tenant")
    entry: AuditLog = audit(action="x", request=request)
    assert entry.tenant_id == "header-tenant"


def test_audit_falls_back_to_subdomain_via_resolve_tenant_id(rf: RequestFactory) -> None:
    request = rf.get("/x", HTTP_HOST="acme.example.com")
    entry: AuditLog = audit(action="x", request=request)
    assert entry.tenant_id == "acme"


def test_audit_no_tenant_when_neither_context_nor_request_provides_one(rf: RequestFactory) -> None:
    request = rf.get("/x")
    entry: AuditLog = audit(action="x", request=request)
    assert entry.tenant_id is None


def test_audit_survives_disallowed_host_during_tenant_resolution(rf: RequestFactory) -> None:
    """``resolve_tenant_id`` calls ``request.get_host()``, which raises
    ``DisallowedHost`` for invalid Host headers. The audit row must still be
    written (with ``tenant_id=None``) rather than failing entirely.
    """
    from unittest.mock import patch

    from django.core.exceptions import DisallowedHost

    request = rf.get("/x", HTTP_HOST="malicious.example.com")
    with patch("observe_kit.audit.utils.resolve_tenant_id", side_effect=DisallowedHost("bad host")):
        entry: AuditLog = audit(action="x", request=request)
    assert entry.pk is not None  # row was created
    assert entry.tenant_id is None  # degraded gracefully


def test_audit_survives_arbitrary_resolver_exception(rf: RequestFactory) -> None:
    """Any exception from the resolver — not just DisallowedHost — must not
    take the audit emission down. Audit logging is more important than the
    tenant tag."""
    from unittest.mock import patch

    request = rf.get("/x")
    with patch(
        "observe_kit.audit.utils.resolve_tenant_id", side_effect=RuntimeError("simulated bug")
    ):
        entry: AuditLog = audit(action="x", request=request)
    assert entry.pk is not None
    assert entry.tenant_id is None
