from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..context import get_request_context
from ..metrics import AUDIT_EVENTS
from .models import AuditLog

logger = logging.getLogger(__name__)


def audit(actor=None, action: str = "", obj: Optional[Any] = None, extra: Optional[Dict[str, Any]] = None, request=None) -> AuditLog:
    context = get_request_context()
    tenant_id = context.tenant_id or getattr(getattr(request, "tenant", None), "id", None)
    remote_addr = context.remote_addr or (request.META.get("REMOTE_ADDR") if request else None)
    user_agent = context.user_agent or (request.META.get("HTTP_USER_AGENT") if request else None)

    entry = AuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=obj.__class__.__name__ if obj else None,
        object_id=str(getattr(obj, "pk", None) or getattr(obj, "id", None) or "") or None,
        tenant_id=str(tenant_id) if tenant_id else None,
        remote_addr=remote_addr,
        user_agent=user_agent,
        extra=extra or {},
    )
    AUDIT_EVENTS.labels(tenant=str(tenant_id or "unknown")).inc()
    logger.info(
        "audit_event",
        extra={
            "event": "audit_event",
            "action": action,
            "tenant_id": tenant_id,
            "actor": getattr(actor, "id", None),
            "object": entry.object_type,
        },
    )
    return entry
