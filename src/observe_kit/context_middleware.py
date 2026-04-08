from __future__ import annotations

import importlib.util
import logging
from typing import Any, Optional

from django.utils.deprecation import MiddlewareMixin

from .conf import PII_SINK_LOGS
from .context import RequestContext, RequestTiming, get_request_context, set_request_context
from .metrics.db import QueryRecorder, wrap_connections
from .pii_rules import PiiLevel, effective_sets, get_pii_config, sanitize_mapping
from .settings import get_observe_kit_settings
from .tenant import resolve_tenant_id
from .typing import DjangoRequest, DjangoResponse

logger = logging.getLogger(__name__)

_WAGTAIL_INSTALLED: bool = importlib.util.find_spec("wagtail") is not None


class RequestContextMiddleware(MiddlewareMixin):
    """Build and store request context for each Django request."""

    pii_level: Optional[PiiLevel]

    def __init__(self, get_response: Optional[Any] = None, pii_level: Optional[str] = None) -> None:
        """Initialize request context middleware.

        Args:
            get_response: Django middleware get_response callable
            pii_level: Optional PII level for context headers/params
                      (defaults to 'logs' sink level).
                      If None, uses the per-sink PII configuration.
        """
        super().__init__(get_response)
        if pii_level is not None:
            self.pii_level = PiiLevel(pii_level)
        else:
            # Use logs sink level as default for context storage
            self.pii_level = None

        # Snapshot config at init — static for the process lifetime.
        cfg = get_observe_kit_settings()
        self._hash_salt = cfg.pii_hash_salt
        self._trusted_proxies = cfg.trusted_proxies
        self._db_tracking = cfg.db_tracking
        # Pre-compute merged PII sets once so process_request skips set unions per request.
        self._drop, self._mask, self._hsh = effective_sets(
            cfg.extra_drop_headers, cfg.extra_mask_fields, cfg.extra_hash_fields
        )

    def process_request(self, request: DjangoRequest) -> None:
        try:
            context = RequestContext()
            context.method = request.method
            context.path = request.path
            context.remote_addr = _resolve_remote_addr(request, self._trusted_proxies)
            context.user_agent = request.META.get("HTTP_USER_AGENT")

            # Use per-sink PII config if available, otherwise use instance level
            if self.pii_level is None:
                pii_config = get_pii_config()
                level = pii_config.get_level(PII_SINK_LOGS)
            else:
                level = self.pii_level

            context.headers = sanitize_mapping(
                getattr(request, "headers", {}),
                level,
                self._drop,
                self._mask,
                self._hsh,
                self._hash_salt,
            )
            context.query_params = sanitize_mapping(
                getattr(request, "GET", {}),
                level,
                self._drop,
                self._mask,
                self._hsh,
                self._hash_salt,
            )
            context.user_id = _safe_str(getattr(getattr(request, "user", None), "id", None))
            context.tenant_id = resolve_tenant_id(request)

            # Detect framework
            context.framework = _detect_framework(request)

            request._observe_kit_context = context
            set_request_context(context)
            request._observe_kit_timer = RequestTiming()

            if self._db_tracking:
                request._observe_kit_queries = QueryRecorder()
                request._observe_kit_remove_wrappers = wrap_connections(
                    request._observe_kit_queries
                )
            else:
                request._observe_kit_queries = None
                request._observe_kit_remove_wrappers = None
        except Exception as e:
            logger.warning(
                "Failed to initialize request context", extra={"error": str(e)}, exc_info=True
            )
            # Create minimal fallback context
            context = RequestContext()
            context.method = getattr(request, "method", None)
            context.path = getattr(request, "path", None)
            set_request_context(context)

    def process_view(
        self, request: DjangoRequest, view_func: Any, view_args: Any, view_kwargs: Any
    ) -> None:
        context = get_request_context()
        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match and resolver_match.route:
            context.route = resolver_match.route
        elif resolver_match and resolver_match.view_name:
            context.route = resolver_match.view_name

    def process_exception(self, request: DjangoRequest, exception: Exception) -> None:
        """Ensure DB wrappers are removed even when the view raises an exception."""
        remover = getattr(request, "_observe_kit_remove_wrappers", None)
        if callable(remover):
            try:
                remover()
            except Exception as e:
                logger.warning(
                    "Failed to remove DB wrappers on exception",
                    extra={"error": str(e)},
                    exc_info=True,
                )
            finally:
                setattr(request, "_observe_kit_remove_wrappers", None)

    def process_response(self, request: DjangoRequest, response: DjangoResponse) -> DjangoResponse:
        try:
            context = get_request_context()
            context.status = getattr(response, "status_code", None)
            context.duration_ms = (
                request._observe_kit_timer.stop()
                if hasattr(request, "_observe_kit_timer")
                else None
            )
            if (
                hasattr(request, "_observe_kit_queries")
                and request._observe_kit_queries is not None
            ):
                context.db_queries = request._observe_kit_queries.count
                context.db_time_ms = request._observe_kit_queries.total_time * 1000
            remover = getattr(request, "_observe_kit_remove_wrappers", None)
            if callable(remover):
                remover()
        except Exception as e:
            logger.warning(
                "Failed to finalize request context", extra={"error": str(e)}, exc_info=True
            )
        return response


class UserLoggingContextMiddleware(MiddlewareMixin):
    """Expose the request context to all log entries during a request."""

    def process_request(self, request: DjangoRequest) -> None:
        if hasattr(request, "_observe_kit_context"):
            set_request_context(request._observe_kit_context)


def _safe_str(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    value_str = str(value)
    return value_str or None


def _resolve_remote_addr(request: DjangoRequest, trusted_proxies: list[str]) -> Optional[str]:
    """Return the originating client IP, honouring X-Forwarded-For for trusted proxies."""
    remote_addr: Optional[str] = request.META.get("REMOTE_ADDR") or None
    if not trusted_proxies:
        return remote_addr
    if trusted_proxies == ["*"] or remote_addr in trusted_proxies:
        xff: Optional[str] = request.META.get("HTTP_X_FORWARDED_FOR") or None
        if xff:
            return xff.split(",")[0].strip() or None
    return remote_addr


def _detect_framework(request: DjangoRequest) -> Optional[str]:
    """Detect the framework/interface for the request.

    Returns:
        - "wagtail_admin" for Wagtail admin requests
        - "django_admin" for Django admin requests
        - None for regular requests
    """
    path = request.path

    if _WAGTAIL_INSTALLED:
        # Wagtail admin typically uses /admin/ or /cms-admin/
        if path.startswith("/admin/") or path.startswith("/cms-admin/"):
            # Additional check: verify it's actually Wagtail admin
            try:
                import wagtail  # noqa: F401

                return "wagtail_admin"
            except ImportError:
                pass

    # Check for Django admin
    if path.startswith("/admin/"):
        return "django_admin"

    return None
