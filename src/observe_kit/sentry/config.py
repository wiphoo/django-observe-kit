from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from ..conf import PII_SINK_SENTRY
from ..pii_rules import PiiLevel, get_pii_config, sanitize_headers

logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when configuration is invalid."""

    pass


def _validate_dsn(dsn: str) -> None:
    """Validate Sentry DSN."""
    if not dsn or not isinstance(dsn, str):
        raise ConfigurationError("dsn must be a non-empty string")
    if not dsn.startswith(("http://", "https://")):
        raise ConfigurationError(
            "dsn must be a valid Sentry DSN URL starting with http:// or https://"
        )
    try:
        urlparse(dsn)
    except Exception as e:
        raise ConfigurationError(f"dsn must be a valid URL: {e}") from e


def _validate_environment(environment: str) -> None:
    """Validate environment name."""
    if not environment or not isinstance(environment, str):
        raise ConfigurationError("environment must be a non-empty string")
    if len(environment) > 64:
        raise ConfigurationError("environment must be 64 characters or less")


def _validate_traces_sample_rate(traces_sample_rate: float) -> None:
    """Validate traces sample rate."""
    if not isinstance(traces_sample_rate, (int, float)):
        raise ConfigurationError("traces_sample_rate must be a number")
    if not 0.0 <= traces_sample_rate <= 1.0:
        raise ConfigurationError("traces_sample_rate must be between 0.0 and 1.0")


def scrub_event(
    event: Dict[str, Any], hint: Optional[Dict[str, Any]] = None, level: PiiLevel = PiiLevel.BASIC
) -> Dict[str, Any]:
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    request["headers"] = sanitize_headers(headers, level)
    event["request"] = request
    return event


def init_sentry(
    dsn: str,
    environment: str,
    traces_sample_rate: float = 0.1,
    before_send: Optional[
        Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Dict[str, Any]]
    ] = None,
    pii_level: Optional[PiiLevel] = None,
) -> None:
    """Initialize Sentry with Django integration and PII scrubbing.

    Args:
        dsn: Sentry DSN (required, must be valid Sentry DSN URL)
        environment: Environment name (required, max 64 chars, e.g., 'dev', 'prod')
        traces_sample_rate: Sample rate for traces (0.0 to 1.0, default 0.1)
        before_send: Optional custom before_send callback
        pii_level: Optional PII level for Sentry. If None, uses per-sink PII configuration.

    Raises:
        ConfigurationError: If any configuration parameter is invalid
    """
    # Validate configuration
    _validate_dsn(dsn)
    _validate_environment(environment)
    _validate_traces_sample_rate(traces_sample_rate)

    # Use per-sink PII config if pii_level not explicitly provided
    if pii_level is None:
        pii_config = get_pii_config()
        pii_level = pii_config.get_level(PII_SINK_SENTRY)

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[DjangoIntegration()],
        traces_sample_rate=traces_sample_rate,
        before_send=before_send or (lambda event, hint: scrub_event(event, hint, pii_level)),  # type: ignore[arg-type]
    )
    logger.info("sentry configured", extra={"environment": environment, "pii_level": pii_level})
