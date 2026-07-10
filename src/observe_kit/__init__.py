"""Django Observe Kit
------------------

A lightweight observability toolkit for Django, DRF, and Wagtail projects. The
library centralizes request context handling, structured logging, tracing,
metrics, and audit logging utilities so applications can adopt observability
best practices with minimal configuration.
"""

from importlib.metadata import PackageNotFoundError, version

from .context import RequestContext, get_request_context, reset_request_context, set_request_context
from .context_middleware import RequestContextMiddleware, UserLoggingContextMiddleware
from .logging import (
    RequestContextFilter,
    RequestLoggingMiddleware,
    configure_logging,
    log_request_complete,
)
from .logging.config import ConfigurationError as LoggingConfigurationError
from .metrics import PrometheusRequestMiddleware, metrics_view
from .otel.config import ConfigurationError as OtelConfigurationError
from .otel.config import init_tracing
from .pii_rules import (
    PiiConfig,
    PiiLevel,
    get_pii_config,
    sanitize_headers,
    sanitize_query_params,
    set_pii_config,
)
from .sentry import SentryContextMiddleware, init_sentry
from .sentry.config import ConfigurationError as SentryConfigurationError
from .tenant import resolve_tenant_id

__all__ = [
    "RequestContext",
    "RequestContextFilter",
    "RequestContextMiddleware",
    "RequestLoggingMiddleware",
    "PiiConfig",
    "PiiLevel",
    "PrometheusRequestMiddleware",
    "SentryContextMiddleware",
    "UserLoggingContextMiddleware",
    "ConfigurationError",
    "LoggingConfigurationError",
    "SentryConfigurationError",
    "configure_logging",
    "get_pii_config",
    "get_request_context",
    "init_sentry",
    "init_tracing",
    "log_request_complete",
    "metrics_view",
    "reset_request_context",
    "resolve_tenant_id",
    "sanitize_headers",
    "sanitize_query_params",
    "set_pii_config",
    "set_request_context",
]

# Export ConfigurationError (all modules use the same exception class)
ConfigurationError = OtelConfigurationError

try:
    # `importlib.metadata.version` resolves the *distribution* name (the PyPI
    # package), not the import name. The distribution is `django-observe-kit`.
    __version__ = version("django-observe-kit")
except PackageNotFoundError:  # pragma: no cover - fallback when metadata missing
    # Use a PEP 440 local segment so code branching on `__version__` can
    # distinguish a fallback from a genuinely-tagged 0.0.0 release.
    __version__ = "0.0.0+unknown"
    # NB: the module-level name `logging` has been rebound to the
    # `observe_kit.logging` submodule by the `from .logging import ...` above,
    # so reach for the stdlib logger explicitly here.
    import logging as _stdlib_logging

    _stdlib_logging.getLogger(__name__).warning(
        "observe_kit: package metadata not found; __version__ falls back to %s", __version__
    )
