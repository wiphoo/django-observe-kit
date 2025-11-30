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
