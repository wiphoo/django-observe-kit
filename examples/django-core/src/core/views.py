from __future__ import annotations

import logging

from django.http import JsonResponse

from observe_kit.context import get_request_context

logger = logging.getLogger(__name__)


def home(_request):
    context = get_request_context()
    logger.info("core_home_view", extra={"event": "core_home_view"})
    return JsonResponse(
        {
            "service": "example-django-core",
            "observability": {
                "trace_id": context.trace_id,
                "route": context.route,
            },
        }
    )


def health(_request):
    return JsonResponse({"ok": True})
