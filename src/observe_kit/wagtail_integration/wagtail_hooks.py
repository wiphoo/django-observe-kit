from __future__ import annotations

import importlib.util
import logging

from opentelemetry import trace

from ..audit import audit
from ..context import get_request_context
from ..metrics import WAGTAIL_DELETED, WAGTAIL_PUBLISHED, WAGTAIL_UNPUBLISHED
from ..otel.config import enrich_span

logger = logging.getLogger(__name__)


if importlib.util.find_spec("wagtail"):
    from wagtail import hooks

    tracer = trace.get_tracer(__name__)

    def _with_span(name: str, func):
        def wrapper(page, request, **kwargs):
            with tracer.start_as_current_span(name) as span:
                enrich_span(span)
                return func(page, request, span=span, **kwargs)

        return wrapper

    @hooks.register("after_publish_page")
    def audit_publish_page(request, page):
        context = get_request_context()
        tenant = context.tenant_id or "unknown"
        WAGTAIL_PUBLISHED.labels(tenant).inc()
        audit(actor=getattr(request, "user", None), action="publish", obj=page, request=request)
        logger.info("wagtail_publish", extra={"event": "wagtail_publish", "page": page.id, "tenant_id": tenant})

    @hooks.register("after_unpublish_page")
    def audit_unpublish_page(request, page):
        context = get_request_context()
        tenant = context.tenant_id or "unknown"
        WAGTAIL_UNPUBLISHED.labels(tenant).inc()
        audit(actor=getattr(request, "user", None), action="unpublish", obj=page, request=request)
        logger.info(
            "wagtail_unpublish", extra={"event": "wagtail_unpublish", "page": page.id, "tenant_id": tenant}
        )

    @hooks.register("after_delete_page")
    def audit_delete_page(request, page):
        context = get_request_context()
        tenant = context.tenant_id or "unknown"
        WAGTAIL_DELETED.labels(tenant).inc()
        audit(actor=getattr(request, "user", None), action="delete", obj=page, request=request)
        logger.info("wagtail_delete", extra={"event": "wagtail_delete", "page": page.id, "tenant_id": tenant})
