from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypeAlias

if TYPE_CHECKING:
    from django.http import HttpRequest

    RequestType: TypeAlias = HttpRequest
else:
    RequestType: TypeAlias = object


def resolve_tenant_id(request: RequestType) -> Optional[str]:
    tenant = getattr(request, "tenant", None)
    if tenant:
        tenant_id = getattr(tenant, "id", None)
        if tenant_id is not None:
            return str(tenant_id)
    header_value = request.META.get("HTTP_X_TENANT_ID")
    if header_value:
        return str(header_value)
    host = request.get_host() if hasattr(request, "get_host") else None
    if host and "." in host:
        subdomain = host.split(".")[0]
        if subdomain not in {"www", "localhost"}:
            return str(subdomain)
    return None
