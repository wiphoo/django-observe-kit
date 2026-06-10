from __future__ import annotations

from django.http import JsonResponse

from observe_kit.context import get_request_context


def context_view(_request):
    context = get_request_context()
    return JsonResponse(
        {
            "tenant_id": context.tenant_id,
            "trace_id": context.trace_id,
            "remote_addr": context.remote_addr,
        }
    )
