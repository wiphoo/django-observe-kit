from __future__ import annotations

import importlib.util
import logging
from typing import TYPE_CHECKING, Any, Optional

from django.utils.deprecation import MiddlewareMixin

if TYPE_CHECKING:
    from django.http import HttpRequest

from ..context import get_request_context

logger = logging.getLogger(__name__)


def detect_drf_route(request: "HttpRequest") -> Optional[str]:
    """Detect DRF ViewSet action and return formatted route name.

    Returns format: 'drf.<ViewSet>.<action>' for ViewSets, or None if not a DRF view.
    """
    if not importlib.util.find_spec("rest_framework"):
        return None

    try:
        # First try to get from view instance (available after view is initialized)
        view_instance = getattr(request, "view", None)
        if view_instance:
            from rest_framework.viewsets import ViewSet

            if isinstance(view_instance, ViewSet):
                action = getattr(view_instance, "action", None)
                if action:
                    viewset_name = view_instance.__class__.__name__
                    return f"drf.{viewset_name}.{action}"

        # Fallback: try to detect from resolver_match
        resolver_match = getattr(request, "resolver_match", None)
        if not resolver_match:
            return None

        view_func = getattr(resolver_match, "func", None)
        if not view_func:
            return None

        # Check if it's a DRF view class
        view_cls = getattr(view_func, "cls", None)
        if not view_cls:
            return None

        # Check if it's a ViewSet
        from rest_framework.viewsets import ViewSet

        if not issubclass(view_cls, ViewSet):
            return None

        # Try to get action from view_func.actions (set by @action decorator)
        actions = getattr(view_func, "actions", None)
        if actions and isinstance(actions, dict):
            method = request.method.lower()
            action_name = actions.get(method)
            if action_name:
                viewset_name = view_cls.__name__
                return f"drf.{viewset_name}.{action_name}"

        # Fallback: use standard ViewSet action mapping
        method = request.method.lower()
        action_map = {
            "get": "retrieve" if resolver_match.kwargs.get("pk") else "list",
            "post": "create",
            "put": "update",
            "patch": "partial_update",
            "delete": "destroy",
        }
        action_name = action_map.get(method)
        if action_name:
            viewset_name = view_cls.__name__
            return f"drf.{viewset_name}.{action_name}"

        return None
    except Exception as e:
        logger.debug("Failed to detect DRF route", extra={"error": str(e)})
        return None


def set_drf_action(route: Optional[str]) -> None:
    """Set DRF route/view name onto the shared request context."""

    context = get_request_context()
    context.route = route or context.route


class DRFIntegrationMiddleware(MiddlewareMixin):
    """Middleware to automatically detect and set DRF ViewSet actions."""

    def process_view(
        self, request: "HttpRequest", view_func: Any, view_args: Any, view_kwargs: Any
    ) -> None:
        """Detect DRF ViewSet actions and update context and span."""
        try:
            drf_route = detect_drf_route(request)
            if drf_route:
                set_drf_action(drf_route)

                # Update span name if it exists
                span = getattr(request, "_observe_kit_span", None)
                if span:
                    span.update_name(drf_route)
        except Exception as e:
            logger.warning("Failed to detect DRF action", extra={"error": str(e)}, exc_info=True)
