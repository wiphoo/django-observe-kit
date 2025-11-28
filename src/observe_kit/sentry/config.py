from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from ..pii_rules import PiiLevel, sanitize_headers

logger = logging.getLogger(__name__)


def scrub_event(
    event: Dict[str, Any],
    hint: Optional[Dict[str, Any]] = None,
    level: PiiLevel = PiiLevel.BASIC,
):
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    request["headers"] = sanitize_headers(headers, level)
    event["request"] = request
    return event


def init_sentry(
    dsn: str,
    environment: str,
    traces_sample_rate: float = 0.1,
    before_send: Optional[Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Dict[str, Any]]] = None,
    pii_level: PiiLevel = PiiLevel.BASIC,
) -> None:
    """Initialize Sentry with Django integration and PII scrubbing."""

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[DjangoIntegration()],
        traces_sample_rate=traces_sample_rate,
        before_send=before_send or (lambda event, hint: scrub_event(event, hint, pii_level)),
    )
    logger.info("sentry configured", extra={"environment": environment, "pii_level": pii_level})
