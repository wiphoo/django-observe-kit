from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..conf import PII_SINK_AUDIT
from ..context import get_request_context
from ..metrics import AUDIT_EVENTS, guard_tenant_label
from ..pii_rules import PiiLevel, get_pii_config, sanitize_body
from ..settings import get_observe_kit_settings
from ..tenant import resolve_tenant_id

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from ..typing import DjangoRequest
    from .models import AuditLog

logger = logging.getLogger(__name__)


def audit(
    actor: Optional[AbstractUser] = None,
    action: str = "",
    obj: Optional[Any] = None,
    extra: Optional[Dict[str, Any]] = None,
    request: Optional[DjangoRequest] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> "AuditLog":
    """Create an immutable audit log entry.

    Args:
        actor: The user performing the action.
        action: A short identifier for the action (e.g. ``"page.publish"``).
        obj: The target object — its class name and PK are extracted automatically.
        extra: Arbitrary metadata. PII is sanitised before storage according to
               the ``audit`` sink PII level.
        request: Optional Django request used to resolve ``remote_addr`` /
                 ``user_agent`` when a ``RequestContext`` is unavailable.
        before: Snapshot of the object state *before* the action. PII-sanitised
                and stored under ``extra["_before"]``.
        after: Snapshot of the object state *after* the action. PII-sanitised
               and stored under ``extra["_after"]``.
    """
    # Import here to avoid circular dependency during Django setup
    from .models import AuditLog

    context = get_request_context()
    # Prefer the request-scoped context (set by RequestContextMiddleware).
    # When the audit() call passes ``request`` directly (e.g. management
    # commands, signal handlers), fall back to the canonical resolver so
    # ``HTTP_X_TENANT_ID`` and subdomain-based tenants are honoured
    # consistently with the rest of the stack.
    #
    # The fallback path is wrapped in try/except because ``resolve_tenant_id``
    # may call ``request.get_host()``, which raises ``DisallowedHost`` for
    # invalid Host headers. Losing an audit row over a malformed header would
    # be a worse regression than missing the tenant tag — degrade to None.
    tenant_id = context.tenant_id
    if not tenant_id and request is not None:
        try:
            tenant_id = resolve_tenant_id(request)
        except Exception:  # noqa: BLE001 - audit must never raise on bad input
            logger.debug("audit: tenant resolution failed; recording row without tenant_id")
            tenant_id = None
    remote_addr = context.remote_addr or (request.META.get("REMOTE_ADDR") if request else None)
    user_agent = context.user_agent or (request.META.get("HTTP_USER_AGENT") if request else None)
    trace_id = context.trace_id

    pii_level: PiiLevel = get_pii_config().get_level(PII_SINK_AUDIT)
    cfg = get_observe_kit_settings()

    def _sanitize(data: Any) -> Any:
        return sanitize_body(
            data,
            pii_level,
            extra_drop=cfg.extra_drop_headers,
            extra_mask=cfg.extra_mask_fields,
            extra_hash=cfg.extra_hash_fields,
            hash_salt=cfg.pii_hash_salt,
        )

    sanitised_extra: Dict[str, Any] = dict(_sanitize(extra or {}))
    if before is not None:
        sanitised_extra["_before"] = _sanitize(before)
    if after is not None:
        sanitised_extra["_after"] = _sanitize(after)

    entry: AuditLog = AuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=obj.__class__.__name__ if obj else None,
        object_id=str(getattr(obj, "pk", None) or getattr(obj, "id", None) or "") or None,
        tenant_id=str(tenant_id) if tenant_id else None,
        trace_id=trace_id,
        remote_addr=remote_addr,
        user_agent=user_agent,
        extra=sanitised_extra,
    )
    AUDIT_EVENTS.labels(tenant=guard_tenant_label(str(tenant_id) if tenant_id else None)).inc()
    logger.info(
        "audit_event",
        extra={
            "event": "audit_event",
            "action": action,
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "actor": getattr(actor, "id", None),
            "object": entry.object_type,
        },
    )
    return entry
