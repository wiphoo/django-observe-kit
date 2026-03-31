"""Example views demonstrating observe_kit observability."""

import logging
import time

from django.http import JsonResponse
from django.shortcuts import render

from observe_kit.audit import audit
from observe_kit.context import get_request_context

logger = logging.getLogger(__name__)


def index(request):
    """Simple index view."""
    context = get_request_context()
    logger.info("index_view_accessed", extra={"path": context.path, "method": context.method})
    
    return JsonResponse({
        "message": "Welcome to observe_kit Django example",
        "trace_id": context.trace_id,
        "tenant_id": context.tenant_id,
    })


def user_list(request):
    """Example view that performs database queries."""
    from django.contrib.auth.models import User
    
    # This will be tracked by observe_kit
    users = User.objects.all()[:10]
    
    context = get_request_context()
    logger.info(
        "user_list_view",
        extra={
            "user_count": users.count(),
            "db_queries": context.db_queries,
            "db_time_ms": context.db_time_ms,
        },
    )
    
    # Example audit log
    audit(
        actor=request.user if request.user.is_authenticated else None,
        action="view_user_list",
        obj=None,
        request=request,
    )
    
    return JsonResponse({
        "users": [{"id": u.id, "username": u.username} for u in users],
        "trace_id": context.trace_id,
    })


def slow_view(request):
    """Example view that simulates slow processing."""
    import time
    
    # Simulate processing time
    time.sleep(0.5)
    
    context = get_request_context()
    logger.warning(
        "slow_view_accessed",
        extra={
            "duration_ms": context.duration_ms,
            "warning": "This view is intentionally slow",
        },
    )
    
    return JsonResponse({
        "message": "This view took some time",
        "duration_ms": context.duration_ms,
        "trace_id": context.trace_id,
    })





