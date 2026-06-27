from __future__ import annotations

import ipaddress
import logging
from typing import TYPE_CHECKING, Any, Optional

from django.utils.deprecation import MiddlewareMixin
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

from ..context import get_request_context, reset_request_context, set_request_context
from ..settings import get_observe_kit_settings
from .config import SpanNamer, enrich_span

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


def _client_ip_matches_sources(client_ip: Optional[str], sources: list[str]) -> bool:
    """Return True when ``client_ip`` is covered by any IP / CIDR in ``sources``.

    Malformed entries are silently skipped — operators should not have a
    typo in their config break trace ingest.
    """
    if not client_ip or not sources:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for src in sources:
        try:
            if "/" in src:
                if addr in ipaddress.ip_network(src, strict=False):
                    return True
            elif addr == ipaddress.ip_address(src):
                return True
        except ValueError:
            continue
    return False


class TraceContextMiddleware(MiddlewareMixin):
    """Ensure every request has an OpenTelemetry span and response header.

    Extracts W3C Trace-Context headers from incoming requests to support
    distributed tracing across services.
    """

    def __init__(self, get_response: Optional[Any] = None) -> None:
        super().__init__(get_response)
        self.namer = SpanNamer()

    def _extract_trace_context_with_zero_parent(
        self, traceparent: str, fallback_context: Any
    ) -> Any:
        """Handle W3C traceparent with zero parent_span_id.

        W3C Trace Context spec allows zero parent_span_id to indicate "no parent span"
        while continuing the same trace. OpenTelemetry SDK requires non-zero span_id
        for valid SpanContext, so we create a synthetic parent to maintain trace continuity.
        """
        try:
            # Parse traceparent: version-trace_id-parent_span_id-flags
            parts = traceparent.split("-")
            if len(parts) < 4:
                return fallback_context

            trace_id = int(parts[1], 16)
            parent_span_id = int(parts[2], 16)
            trace_flags = int(parts[3], 16)

            # Only handle zero parent_span_id case
            if parent_span_id != 0:
                return fallback_context

            # Generate synthetic span_id from trace_id (deterministic, non-zero)
            synthetic_span_id = (trace_id & 0xFFFFFFFFFFFFFFFF) or 1

            span_context = SpanContext(
                trace_id=trace_id,
                span_id=synthetic_span_id,
                trace_flags=TraceFlags(trace_flags),
                is_remote=True,
            )

            return trace.set_span_in_context(trace.NonRecordingSpan(span_context), fallback_context)
        except (ValueError, IndexError) as e:
            logger.debug("Failed to parse traceparent header", extra={"error": str(e)})
            return fallback_context

    def process_request(self, request: "HttpRequest") -> None:
        span_context_manager: Optional[Any] = None
        try:
            # Get tracer per-request to ensure we use the current tracer provider
            # (which may be initialized after middleware instantiation in tests)
            tracer = trace.get_tracer(__name__)

            # Extract trace context from incoming request headers
            # Convert Django header format (HTTP_X_TRACE_ID) to standard format (x-trace-id)
            headers = {}
            for key, value in request.META.items():
                if key.startswith("HTTP_"):
                    header_name = key[5:].replace("_", "-").lower()
                    headers[header_name] = value
                elif key in ("traceparent", "tracestate"):
                    headers[key] = value

            # Decide whether the inbound trace context can be trusted.
            # By default we ignore traceparent/tracestate from untrusted edges so
            # attackers can't poison trace storage or force-sample requests.
            #
            # Gating model (AND):
            #   - TRUST_INCOMING_TRACE_CONTEXT=False → never trust (allow-list ignored).
            #   - TRUST_INCOMING_TRACE_CONTEXT=True + empty allow-list → trust every source.
            #   - TRUST_INCOMING_TRACE_CONTEXT=True + non-empty allow-list → trust only
            #     when the resolved client IP matches the allow-list.
            #
            # This keeps "global flag False" as a hard "off" — operators who set it
            # know nothing inbound will be honoured. The allow-list narrows trust
            # when it is enabled rather than re-enabling it when it is disabled.
            cfg = get_observe_kit_settings()
            trust_inbound = False
            if cfg.trust_incoming_trace_context:
                if not cfg.trusted_trace_sources:
                    trust_inbound = True
                else:
                    # Resolve the originating client IP using the canonical
                    # trusted-proxy aware logic so deployments behind a load
                    # balancer evaluate the real client, not the proxy.
                    from ..context_middleware import _resolve_remote_addr

                    client_ip = _resolve_remote_addr(request, cfg.trusted_proxies)
                    trust_inbound = _client_ip_matches_sources(client_ip, cfg.trusted_trace_sources)

            if trust_inbound:
                parent_context = extract(headers)

                # Handle W3C Trace Context edge case: zero parent_span_id
                # W3C spec allows zero parent_span_id to mean "no parent span" while continuing
                # the trace. OpenTelemetry SDK requires non-zero span_id for valid SpanContext,
                # so the propagator returns empty context. We manually create a valid context
                # to maintain trace continuity.
                if "traceparent" in headers:
                    parent_span = trace.get_current_span(parent_context)
                    if not parent_span.get_span_context().is_valid:
                        parent_context = self._extract_trace_context_with_zero_parent(
                            headers.get("traceparent", ""), parent_context
                        )
            else:
                # Start a fresh root context; any inbound traceparent is dropped.
                parent_context = Context()

            # Create span with parent context using start_as_current_span
            # Per OTel semantic conventions, use "{method} {route}" naming pattern
            route = self.namer.name_for_request(request)
            span_name = f"{request.method} {route}"

            # Use SpanKind.SERVER for incoming HTTP requests (per semantic conventions)
            span_context_manager = tracer.start_as_current_span(
                span_name, context=parent_context, kind=SpanKind.SERVER
            )
            span = span_context_manager.__enter__()

            # Set standard HTTP semantic attributes (per OTel semantic conventions)
            # See: https://opentelemetry.io/docs/specs/semconv/http/http-spans/
            span.set_attribute("http.request.method", request.method)
            span.set_attribute("url.path", request.path)
            span.set_attribute("url.scheme", request.scheme)
            # Also set http.scheme for consistency (OTel semantic convention)
            span.set_attribute("http.scheme", request.scheme)
            if request.get_host():
                span.set_attribute("server.address", request.get_host())
                # Also set http.host for consistency (OTel semantic convention)
                span.set_attribute("http.host", request.get_host())
            # Set http.target (full path with query string if present)
            span.set_attribute("http.target", request.get_full_path())
            # Set http.route if available (will be updated later if route is detected)
            if route and route != "unknown":
                span.set_attribute("http.route", route)

            # Verify span context is valid before proceeding
            span_context = span.get_span_context()
            if not span_context.is_valid:
                logger.warning("Created span with invalid context, trace may be incomplete")

            # Store context manager for cleanup in process_response
            # Note: We manually manage the context manager lifecycle because Django
            # middleware doesn't support using 'with' statements across process_request
            # and process_response methods
            request._observe_kit_span_context_manager = span_context_manager

            context = get_request_context()
            span_context = span.get_span_context()
            context.trace_id = format(span_context.trace_id, "032x")
            context.span_id = format(span_context.span_id, "016x")
            # Update http.route if context has a route (e.g., from DRF detection)
            if context.route and context.route != route:
                span.set_attribute("http.route", context.route)
            request._observe_kit_span = span
            set_request_context(context)
        except Exception as e:
            if span_context_manager is not None:
                try:
                    span_context_manager.__exit__(type(e), e, e.__traceback__)
                except Exception:
                    logger.debug("Failed to unwind trace span after setup error", exc_info=True)
            # Log error but don't break the request
            logger.warning("Failed to create trace span", extra={"error": str(e)}, exc_info=True)
            # Create a fallback context without trace info
            context = get_request_context()
            set_request_context(context)

    def process_exception(self, request: "HttpRequest", exception: Exception) -> None:
        """Record exception in the current span.

        Per OTel best practices, exceptions should be recorded in spans to provide
        valuable debugging information in traces.
        """
        span = getattr(request, "_observe_kit_span", None)
        if span:
            span.record_exception(exception)
            span.set_status(Status(StatusCode.ERROR, str(exception)))

    def process_response(self, request: "HttpRequest", response: "HttpResponse") -> "HttpResponse":
        try:
            span = getattr(request, "_observe_kit_span", None)
            if span:
                # Update http.route if it was detected later (e.g., from DRF)
                context = get_request_context()
                if context.route:
                    span.set_attribute("http.route", context.route)

                status_code = getattr(response, "status_code", None)

                # Use semantic convention attribute name
                span.set_attribute("http.response.status_code", status_code)

                # Set span status based on HTTP status code
                # Per OTel semantic conventions:
                # - 2xx: Set to OK (successful operation)
                # - 4xx: Leave as UNSET (client error, not a server failure)
                # - 5xx: Set to ERROR (server error)
                if status_code:
                    if 200 <= status_code < 300:
                        # Preserve earlier error signals recorded during request handling.
                        current_status = getattr(span, "status", None)
                        current_status_code = getattr(current_status, "status_code", None)
                        if current_status_code != StatusCode.ERROR:
                            span.set_status(Status(StatusCode.OK))
                    elif status_code >= 500:
                        # Server errors should be marked as ERROR
                        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
                    # 4xx and other codes remain UNSET (default)

                enrich_span(span)

                # Get trace ID from span before ending it
                span_context = span.get_span_context()
                trace_id = format(span_context.trace_id, "032x")
                response["X-Trace-Id"] = trace_id

                # Exit the context manager to properly end the span
                # This will automatically detach the context set by start_as_current_span
                span_context_manager = getattr(request, "_observe_kit_span_context_manager", None)
                if span_context_manager is not None:
                    span_context_manager.__exit__(None, None, None)
                else:
                    # Fallback: manually end the span if context manager is missing
                    span.end()
        except Exception as e:
            logger.warning("Failed to finalize trace span", extra={"error": str(e)}, exc_info=True)
        finally:
            # Cleanup request context to prevent leaks between requests
            reset_request_context()
        return response
