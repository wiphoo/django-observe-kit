"""PII sanitization of everything this library writes to spans (``otel`` sink).

Every span name, string attribute, exception event and status description goes
through here. At ``NONE`` values are passed on unchanged. Nothing raises, and a
value that can't be scrubbed is replaced by a placeholder, never sent raw.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from opentelemetry.trace import Span, Status, StatusCode

from ..conf import BODY_REDACTED, PII_SINK_OTEL
from ..log_sanitize import _Scrubber
from ..pii_rules import PiiLevel, get_pii_config


def sanitizing_spans() -> bool:
    """False at ``NONE``, where spans must look exactly as without sanitization."""
    return get_pii_config().get_level(PII_SINK_OTEL) != PiiLevel.NONE


def scrub_span_text(value: str) -> str:
    """Mask emails and scrub URL credentials in a span name or string value."""
    try:
        level = get_pii_config().get_level(PII_SINK_OTEL)
        if level == PiiLevel.NONE:
            return value
        return _Scrubber(level).text(value)
    except Exception:  # noqa: BLE001 - never raise into the request, never emit raw
        return BODY_REDACTED


def scrub_span_value(value: Any) -> Any:
    """:func:`scrub_span_text` for strings; other values pass through."""
    return scrub_span_text(value) if isinstance(value, str) else value


def _render(render: Callable[[], str]) -> tuple[str, bool]:
    """``(text safe to export, whether it is the unmodified text)``."""
    try:
        raw = render()
        clean = scrub_span_text(raw)
        return clean, clean == raw
    except Exception:  # noqa: BLE001 - e.g. a broken __str__
        return BODY_REDACTED, False


def _type_name(cls: type) -> str:
    """``exception.type`` as the SDK spells it."""
    if cls.__module__ and cls.__module__ != "builtins":
        return f"{cls.__module__}.{cls.__qualname__}"
    return cls.__qualname__


def record_span_exception(span: Span, exception: BaseException) -> str:
    """``span.record_exception`` with a scrubbed event; return the scrubbed ``str(exception)``.

    The caller must have checked :func:`sanitizing_spans`. The stack trace is
    scrubbed as one rendered text, which covers causes, contexts, notes and
    exception groups. Never raises.
    """
    message, message_ok = _render(lambda: str(exception))
    try:
        cls = type(exception)
        exc_type, type_ok = _render(lambda: _type_name(cls))
        stacktrace, trace_ok = _render(
            lambda: "".join(traceback.format_exception(cls, exception, exception.__traceback__))
        )
        if message_ok and type_ok and trace_ok:
            # Nothing to hide, so leave the event to the SDK.
            span.record_exception(exception)
        else:
            span.add_event(
                "exception",
                {
                    "exception.type": exc_type,
                    "exception.message": message,
                    "exception.stacktrace": stacktrace,
                    "exception.escaped": "False",
                },
            )
    except Exception:  # noqa: BLE001 - losing the event is better than breaking the request
        pass
    return message


def record_context_exit_exception(span: Span, exception: BaseException) -> None:
    """What the SDK does when an exception leaves ``start_as_current_span``, but scrubbed.

    For spans started with ``record_exception=False, set_status_on_exception=False``.
    Never raises.
    """
    message = record_span_exception(span, exception)
    name = scrub_span_text(type(exception).__name__)
    span.set_status(Status(StatusCode.ERROR, f"{name}: {message}"))
