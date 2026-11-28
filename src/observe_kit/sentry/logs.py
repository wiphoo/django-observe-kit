"""Sanitize what Sentry derives from application log records.

The SDK's logging integration reads the original ``LogRecord``, not the sanitized
output, so both things it builds are cleaned with :func:`sanitize_record`, found
through the ``log_record`` hint: breadcrumbs in ``before_breadcrumb``, and events in
an event processor. A processor runs before the SDK serializes the event (while
``extra`` still holds the objects), before the SDK's ``EventScrubber`` and before
``before_send``, so ``scrub_event`` still applies afterwards, to the user's
additions too.

Levels: the stricter of ``logs`` and ``sentry``. Free text (not dict keys) is left
unhashed when ``sentry`` is ``SENSITIVE``, because ``scrub_event`` scrubs it again at
that level and a hash inside a URL cannot be recognised on a second pass.

Neither hook may raise: the SDK then keeps the raw breadcrumb or event (and
``sentry-sdk`` 2.66 lets a ``before_breadcrumb`` error reach the logging call).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.scope import add_global_event_processor

from ..conf import PII_SINK_LOGS
from ..log_sanitize import LOG_RECORD_CORE_FIELDS, sanitize_record, sanitize_template_and_args
from ..pii_rules import PiiLevel, get_pii_config, marked_digests

_STRICTNESS = (PiiLevel.NONE, PiiLevel.BASIC, PiiLevel.SENSITIVE)


class LogRecordIntegration(Integration):
    """Registers the cleaning of log-created events for the client it is set up on."""

    identifier = "observe_kit_log_records"

    def __init__(self, sentry_level: PiiLevel) -> None:
        self.sentry_level = sentry_level

    @staticmethod
    def setup_once() -> None:
        add_global_event_processor(_process_event)

    def _sanitize(self, record: logging.LogRecord) -> Tuple[logging.LogRecord, PiiLevel, PiiLevel]:
        level = max(
            get_pii_config().get_level(PII_SINK_LOGS), self.sentry_level, key=_STRICTNESS.index
        )
        text_level = PiiLevel.BASIC if self.sentry_level == PiiLevel.SENSITIVE else level
        # Sentry takes the exception from ``exc_info`` itself: don't format it here.
        with marked_digests():  # ``scrub_event`` sees these values again
            clean = sanitize_record(
                record, format_exception=lambda _: "", level=level, text_level=text_level
            )
        return clean, level, text_level

    def before_breadcrumb(
        self, crumb: Dict[str, Any], hint: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            record = hint.get("log_record")
            if record is None:
                return crumb
            clean = self._sanitize(record)[0]
            return {
                **crumb,
                "message": clean.getMessage(),
                "data": {k: v for k, v in clean.__dict__.items() if k in crumb.get("data", ())},
            }
        except BaseException:  # noqa: BLE001 - drop the breadcrumb, never keep it raw
            return None

    def clean_event(self, event: Dict[str, Any], record: logging.LogRecord) -> None:
        clean, level, text_level = self._sanitize(record)
        extra = event.get("extra")
        if isinstance(extra, dict):
            for key in list(extra):
                if key in record.__dict__ and key not in LOG_RECORD_CORE_FIELDS:
                    if key in clean.__dict__:
                        extra[key] = clean.__dict__[key]
                    else:
                        del extra[key]
        logentry = event.get("logentry")
        if isinstance(logentry, dict):
            template, args = sanitize_template_and_args(
                record, clean, logentry.get("message"), level, text_level
            )
            cleaned = {"message": template, "formatted": clean.getMessage()}
            if args is not None:
                cleaned["params"] = args
            event["logentry"] = cleaned


def _process_event(event: Any, hint: Any) -> Any:
    try:
        record = hint.get("log_record")
        if record is None:
            return event
        integration = sentry_sdk.get_client().get_integration(LogRecordIntegration)
        if integration is not None:
            integration.clean_event(event, record)
        return event
    except BaseException:  # noqa: BLE001 - drop the event, never send it raw
        return None
