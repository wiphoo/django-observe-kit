"""PII sanitization of application log records for the ``logs`` sink.

One seam, :func:`sanitize_record`, used by every output this library configures:
``RequestFormatter`` (JSON console) and ``_SafeOTelLogHandler`` (OTel log export).
It returns a sanitized *copy*. The caller's record, ``extra`` objects and ``args``
are never modified, so other handlers see the original and nothing is scrubbed twice.

It never raises and never logs. If anything fails, the output gets a fixed
placeholder with the record's logger / level / source location only.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import decimal
import enum
import logging
import threading
import traceback
import types
import uuid
from collections.abc import Mapping
from typing import Any, Callable, Dict, List, Optional, Set

from .conf import BODY_REDACTED, BODY_SECRET_KEYS, PII_SINK_LOGS
from .pii_rules import PiiLevel, get_pii_config, sanitize_body
from .sentry.config import _PiiOpts, _scrub_text
from .sentry.scrub.emails import _dedupe_masked_key
from .settings import get_observe_kit_settings

# Standard ``LogRecord`` attributes; every other attribute came from ``extra``
# (or a filter) and gets the field rules.
LOG_RECORD_CORE_FIELDS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
        "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
        "relativeCreated", "thread", "threadName", "processName", "process", "message",
        "asctime", "taskName",
    }
)  # fmt: skip
_PLACEHOLDER = f"{BODY_REDACTED} observe_kit: log record could not be sanitized"
# The metadata a placeholder record keeps: nothing that carries application data.
_PLACEHOLDER_FIELDS = LOG_RECORD_CORE_FIELDS - {
    "msg", "args", "exc_info", "exc_text", "stack_info", "message", "asctime",
}  # fmt: skip
# Every vector ``_scrub_text`` handles needs one of these, so other strings skip it.
_SCRUB_TRIGGERS = ("@", "?", "#", "%", "//")
_MAX_DEPTH = 50
_SCALARS = (
    type(None), bool, int, float, complex, decimal.Decimal, uuid.UUID,
    datetime.date, datetime.time, datetime.timedelta,
)  # fmt: skip
_state = threading.local()


def render_text(value: Any) -> str:
    """Render an unknown object the way the JSON encoder would."""
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    if isinstance(value, types.TracebackType):
        return "".join(traceback.format_tb(value))
    return str(value)


def _plain(
    value: Any, render: Callable[[Any], str], path: Set[int], hidden: Optional[List[bool]] = None
) -> Any:
    """Copy ``value`` into dicts, lists, strings and scalars, so the key rules can see inside.

    ``hidden`` is appended to when the original's ``repr`` can show more than the copy.
    """
    if isinstance(value, enum.Enum):
        return _plain(value.value, render, path, hidden)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, type):
        return value.__name__
    if id(value) in path or len(path) >= _MAX_DEPTH:
        if hidden is not None:
            hidden.append(True)
        return BODY_REDACTED  # cycle or absurd nesting
    key = id(value)  # ``value`` is rebound below; remove the same id that was added
    path.add(key)
    try:
        if isinstance(value, tuple) and hasattr(value, "_asdict"):
            value = value._asdict()
        elif dataclasses.is_dataclass(value):
            value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
        if isinstance(value, Mapping):
            if hidden is not None and hasattr(value, "lists"):
                hidden.append(True)  # a multi-value dict: ``.items()`` is the last value only
            return {
                k if isinstance(k, (str, int)) else render(k): _plain(v, render, path, hidden)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_plain(item, render, path, hidden) for item in value]
        try:
            return render(value)
        except Exception:  # noqa: BLE001 - a broken __str__ must not lose the whole record
            return BODY_REDACTED
    finally:
        path.discard(key)


class _Scrubber:
    def __init__(self, level: PiiLevel, render: Callable[[Any], str]) -> None:
        cfg = get_observe_kit_settings()
        self.render = render
        self.opts = _PiiOpts(
            level=level,
            hash_salt=cfg.pii_hash_salt,
            extra_drop=cfg.extra_drop_headers,
            extra_mask=cfg.extra_mask_fields,
            extra_hash=cfg.extra_hash_fields,
        )

    def text(self, value: str) -> str:
        """Mask emails and scrub URLs (credential query values, userinfo) in free text."""
        if not any(trigger in value for trigger in _SCRUB_TRIGGERS):
            return value
        return _scrub_text(value, self.opts)

    def leaves(self, node: Any) -> Any:
        if isinstance(node, str):
            return self.text(node)
        if isinstance(node, dict):
            result: Dict[Any, Any] = {}
            for key, value in node.items():
                new_key = self.text(key) if isinstance(key, str) else key
                result[_dedupe_masked_key(result, new_key)] = self.leaves(value)
            return result
        if isinstance(node, list):
            return [self.leaves(item) for item in node]
        return node

    def _rules(self, plain: Any) -> Any:
        """Field rules plus credential redaction, applied to the original values."""
        opts = self.opts
        return sanitize_body(
            plain,
            opts.level,
            extra_drop=opts.extra_drop,
            extra_mask=opts.extra_mask,
            extra_hash=opts.extra_hash,
            hash_salt=opts.hash_salt,
            redact_keys=BODY_SECRET_KEYS,
        )

    def structure(self, value: Any) -> Any:
        return self.leaves(self._rules(_plain(value, self.render, set())))

    def arg(self, value: Any) -> Any:
        """A ``%`` argument: only a container a field rule changes is replaced.

        Everything else is passed through so ``%d`` / ``%r`` format as before; the
        formatted message gets the text scrub.
        """
        if not isinstance(value, (Mapping, list, tuple, set, frozenset)) and not (
            dataclasses.is_dataclass(value)
        ):
            return value
        hidden: List[bool] = []
        plain = _plain(value, self.render, set(), hidden)
        cleaned = self._rules(plain)
        return value if cleaned == plain and not hidden else cleaned


def _sanitize(
    record: logging.LogRecord,
    scrubber: _Scrubber,
    format_exception: Callable[[Any], Any],
    exception_attributes: bool,
) -> logging.LogRecord:
    clean = copy.copy(record)
    data = clean.__dict__

    extras = {k: v for k, v in data.items() if k not in LOG_RECORD_CORE_FIELDS}
    for key in extras:
        del data[key]
    data.update(scrubber.structure(extras))

    msg = clean.msg
    if not clean.args and (
        isinstance(msg, (Mapping, list, tuple)) or dataclasses.is_dataclass(msg)
    ):
        clean.msg = scrubber.structure(msg)
    else:
        # Format first, then scrub: a secret may be split between ``msg`` and ``args``.
        if isinstance(clean.args, tuple):
            clean.args = tuple(scrubber.arg(item) for item in clean.args)
        elif clean.args:
            clean.args = scrubber.arg(clean.args)
        clean.msg = scrubber.text(clean.getMessage())
    clean.args = None
    data.pop("message", None)  # cached raw text from an earlier formatter

    if clean.exc_info:
        # A list when the JSON formatter is set to ``exc_info_as_array``.
        clean.exc_text = scrubber.leaves(format_exception(clean.exc_info))
        if exception_attributes:
            # The OTel SDK derives these from ``exc_info``, which is removed below.
            exc_type, exc, _ = clean.exc_info
            if exc_type is not None:
                data["exception.type"] = exc_type.__name__
            if exc is not None and exc.args:
                data["exception.message"] = scrubber.text(str(exc.args[0]))
            data["exception.stacktrace"] = clean.exc_text
        clean.exc_info = None
    elif clean.exc_text:
        clean.exc_text = scrubber.text(clean.exc_text)
    if clean.stack_info:
        clean.stack_info = scrubber.text(clean.stack_info)
    return clean


def redacted_record(record: logging.LogRecord) -> logging.LogRecord:
    """A placeholder for a record that could not be sanitized."""
    placeholder = logging.LogRecord("", 0, "", 0, _PLACEHOLDER, None, None)
    placeholder.__dict__.update(
        {k: v for k, v in record.__dict__.items() if k in _PLACEHOLDER_FIELDS}
    )
    return placeholder


def sanitize_record(
    record: logging.LogRecord,
    format_exception: Callable[[Any], Any],
    render: Callable[[Any], str] = render_text,
    exception_attributes: bool = False,
) -> logging.LogRecord:
    """Return a copy of ``record`` sanitized at the ``logs`` PII level. Never raises.

    ``record`` itself is returned at ``NONE``.
    """
    try:
        level = get_pii_config().get_level(PII_SINK_LOGS)
        if level == PiiLevel.NONE:
            return record
        if getattr(_state, "active", False):
            # Something rendered by the sanitizer logged: don't recurse into it.
            return redacted_record(record)
        _state.active = True
        try:
            scrubber = _Scrubber(level, render)
            return _sanitize(record, scrubber, format_exception, exception_attributes)
        finally:
            _state.active = False
    except Exception:  # noqa: BLE001 - never raise from inside logging, never emit raw
        return redacted_record(record)
