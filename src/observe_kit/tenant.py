from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional, TypeAlias

from .proxies import is_trusted_peer
from .settings import get_observe_kit_settings

if TYPE_CHECKING:
    from django.http import HttpRequest

    RequestType: TypeAlias = HttpRequest
else:
    RequestType: TypeAlias = object


_TENANT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")  # 128 = AuditLog.tenant_id max_length


def _header_tenant(request: RequestType) -> Optional[str]:
    """Return ``X-Tenant-ID`` only when opted in, sent by a trusted proxy, and well-formed."""
    cfg = get_observe_kit_settings()
    if not cfg.tenant_header_trusted:
        return None
    peer = request.META.get("REMOTE_ADDR")
    if not is_trusted_peer(peer, cfg.trusted_proxies):
        return None
    value = request.META.get("HTTP_X_TENANT_ID")
    if value and _TENANT_ID_RE.fullmatch(value):
        return str(value)
    return None


def resolve_tenant_id(request: RequestType) -> Optional[str]:
    tenant = getattr(request, "tenant", None)
    if tenant:
        tenant_id = getattr(tenant, "id", None)
        if tenant_id is not None:
            return str(tenant_id)
    header_tenant = _header_tenant(request)
    if header_tenant:
        return header_tenant
    host = request.get_host() if hasattr(request, "get_host") else None
    if host and "." in host:
        parts = host.split(".")
        # Only treat as subdomain if there are at least 3 parts (subdomain.domain.tld)
        # or if it's clearly a subdomain pattern (not just domain.tld)
        if len(parts) >= 3:
            subdomain = parts[0]
            if subdomain not in {"www", "localhost"}:
                return str(subdomain)
        # For domain.tld format (2 parts), don't treat as tenant
        # This handles cases like "example.com" -> None
    return None
