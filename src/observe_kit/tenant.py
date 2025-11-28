from __future__ import annotations

from typing import Optional


def resolve_tenant_id(request) -> Optional[str]:
    tenant = getattr(request, "tenant", None)
    if tenant and getattr(tenant, "id", None):
        return str(tenant.id)
    header_value = request.META.get("HTTP_X_TENANT_ID")
    if header_value:
        return str(header_value)
    host = request.get_host() if hasattr(request, "get_host") else None
    if host and "." in host:
        subdomain = host.split(".")[0]
        if subdomain not in {"www", "localhost"}:
            return subdomain
    return None
