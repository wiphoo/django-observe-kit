"""PII sanitization of application log records for the ``logs`` sink.

One seam, :func:`sanitize_record`, used by every output this library configures:
``RequestFormatter`` (JSON console), ``_SafeOTelLogHandler`` (OTel log export) and
what Sentry derives from a record (:mod:`observe_kit.sentry.logs`).
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
from collections.abc import Mapping, Sequence, Set, ValuesView
from dataclasses import replace
from typing import Any, Callable, Dict, Optional, Tuple

from .conf import BODY_REDACTED, BODY_SECRET_KEYS, PII_SINK_LOGS, cgi_header_name
from .pii_rules import PiiLevel, get_pii_config, sanitize_body
from .sentry.config import _PiiOpts, _scrub_query_string, _scrub_text
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
# Entries and items copied per record and output. Bounds shared references and dense
# cycles, whose expansion is exponential even within ``_MAX_DEPTH``.
_MAX_NODES = 20_000
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


def _is_container(value: Any) -> bool:
    """True for values :func:`_plain` opens, so the key rules can see inside."""
    if isinstance(value, (str, bytes, bytearray, type)):
        return False
    containers = (Mapping, Sequence, Set, ValuesView)
    return isinstance(value, containers) or dataclasses.is_dataclass(value)


class _Walk:
    """State of one record's copy: the recursion path and the node budget."""

    def __init__(self) -> None:
        self.path: set[int] = set()
        self.nodes = _MAX_NODES
        # Set when an original's ``repr`` can show more than its copy.
        self.hidden = False

    def spend(self) -> bool:
        self.nodes -= 1
        return self.nodes >= 0


def _plain_key(key: Any, walk: _Walk) -> Optional[Any]:
    """A mapping key as the key rules and the output see it; ``None`` if it can't be read."""
    if key is None or type(key) in (str, int, float, bool):
        return key
    walk.hidden = True  # the original key prints differently, and two keys may now collide
    if isinstance(key, enum.Enum):
        return _plain_key(key.value, walk)
    if isinstance(key, str):
        return str.__str__(key)
    if isinstance(key, (bytes, bytearray)):
        return bytes(key).decode("utf-8", "replace")
    try:
        return str(key)
    except Exception:  # noqa: BLE001 - a broken __str__ must not lose the whole record
        return None


def _plain(value: Any, walk: _Walk) -> Any:
    """Copy ``value`` into dicts, lists, strings and scalars, within the node budget."""
    if isinstance(value, enum.Enum):
        return _plain(value.value, walk)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, type):
        return value.__name__
    if not _is_container(value):
        try:
            return render_text(value)
        except Exception:  # noqa: BLE001 - a broken __str__ must not lose the whole record
            return BODY_REDACTED
    if id(value) in walk.path or len(walk.path) >= _MAX_DEPTH or walk.nodes < 0:
        walk.hidden = True
        return BODY_REDACTED  # cycle, absurd nesting or budget spent
    identity = id(value)  # ``value`` is rebound below; remove the same id that was added
    walk.path.add(identity)
    try:
        if isinstance(value, tuple) and hasattr(value, "_asdict"):
            value = value._asdict()
        elif dataclasses.is_dataclass(value):
            # As in the generated ``repr``: ``field(repr=False)`` stays hidden.
            value = {
                f.name: getattr(value, f.name)
                for f in dataclasses.fields(value)
                if f.repr and hasattr(value, f.name)
            }
        if isinstance(value, Mapping):
            if hasattr(value, "lists"):
                walk.hidden = True  # a multi-value dict: ``.items()`` is the last value only
            result: Dict[Any, Any] = {}
            for key, item in value.items():
                if not walk.spend():
                    walk.hidden = True
                    result[BODY_REDACTED] = BODY_REDACTED
                    break
                name = _plain_key(key, walk)
                if name is None:
                    result[BODY_REDACTED] = BODY_REDACTED
                else:
                    result[name] = _plain(item, walk)
            return result
        items = []
        for item in value:
            if not walk.spend():
                break
            items.append(_plain(item, walk))
        if walk.nodes < 0:
            # Cut short anywhere below: a partly filtered pair list is no longer read
            # as pairs, which would leave its earlier values raw. Drop the whole list.
            walk.hidden = True
            return BODY_REDACTED
        return items
    finally:
        walk.path.discard(identity)


def _key_alias(key: str) -> Optional[str]:
    """The header name a ``request.META`` key (or cookie container) is ruled as."""
    if key.endswith("_COOKIE") or key.lower() == "cookies":
        return "cookie"
    return cgi_header_name(key)


class _Scrubber:
    def __init__(self, level: PiiLevel, text_level: Optional[PiiLevel] = None) -> None:
        cfg = get_observe_kit_settings()
        self.walk = _Walk()
        self.opts = _PiiOpts(
            level=level,
            hash_salt=cfg.pii_hash_salt,
            extra_drop=cfg.extra_drop_headers,
            extra_mask=cfg.extra_mask_fields,
            extra_hash=cfg.extra_hash_fields,
        )
        self.text_opts = self.opts if text_level is None else replace(self.opts, level=text_level)

    def text(self, value: str, opts: Optional[_PiiOpts] = None) -> str:
        """Mask emails and scrub URLs (credential query values, userinfo) in free text."""
        if not any(trigger in value for trigger in _SCRUB_TRIGGERS):
            return value
        return _scrub_text(value, opts or self.text_opts)

    def leaves(self, node: Any) -> Any:
        if isinstance(node, str):
            return self.text(node)
        if isinstance(node, dict):
            result: Dict[Any, Any] = {}
            suffixes: Dict[str, int] = {}
            for key, value in node.items():
                # Full level even when ``text_level`` is lower: no later pass scrubs
                # URLs in keys.
                new_key = self.text(key, self.opts) if isinstance(key, str) else key
                result[_dedupe_masked_key(result, new_key, suffixes)] = self.leaves(value)
            return result
        if isinstance(node, list):
            return [self.leaves(item) for item in node]
        return node

    def _rules(self, plain: Any) -> Any:
        """Field rules plus credential redaction, applied to the original values."""
        opts = self.opts
        cleaned = sanitize_body(
            plain,
            opts.level,
            extra_drop=opts.extra_drop,
            extra_mask=opts.extra_mask,
            extra_hash=opts.extra_hash,
            hash_salt=opts.hash_salt,
            redact_keys=BODY_SECRET_KEYS,
            key_alias=_key_alias,
        )
        return self._query_strings(cleaned)

    def _query_strings(self, node: Any) -> Any:
        """Scrub ``request.META["QUERY_STRING"]``: a bare query the text scrub can't see."""
        if isinstance(node, dict):
            return {k: self._query_entry(k, v) for k, v in node.items()}
        if isinstance(node, list):
            if len(node) == 2 and node[0] == "QUERY_STRING":
                return [node[0], self._query_entry(*node)]
            return [self._query_strings(item) for item in node]
        return node

    def _query_entry(self, key: Any, value: Any) -> Any:
        if key == "QUERY_STRING" and isinstance(value, str) and "=" in value:
            return _scrub_query_string(value, self.opts)
        return self._query_strings(value)

    def structure(self, value: Any) -> Any:
        return self.leaves(self._rules(_plain(value, self.walk)))

    def extras(self, extras: Dict[str, Any]) -> Any:
        """The ``extra`` attributes. Budget is spent inside values only, so that a huge
        one does not remove the record's other attributes."""
        plain = {name: _plain(value, self.walk) for name, value in extras.items()}
        return self.leaves(self._rules(plain))

    def arg(self, value: Any) -> Any:
        """A ``%`` argument: only a container a field rule changes is replaced.

        Everything else is passed through so ``%d`` / ``%r`` format as before; the
        formatted message gets the text scrub.
        """
        if not _is_container(value):
            return value
        self.walk.hidden = False
        plain = _plain(value, self.walk)
        cleaned = self._rules(plain)
        return value if cleaned == plain and not self.walk.hidden else cleaned


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
    data.update(scrubber.extras(extras))

    if not clean.args and _is_container(clean.msg):
        clean.msg = scrubber.structure(clean.msg)
    else:
        # Format first, then scrub: a secret may be split between ``msg`` and ``args``.
        if isinstance(clean.args, tuple):
            clean.args = tuple(scrubber.arg(item) for item in clean.args)
        elif clean.args:
            clean.args = scrubber.arg(clean.args)
        clean.msg = scrubber.arg(clean.msg)  # a dict message with positional args
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


def sanitizing() -> bool:
    """False at ``NONE``, where outputs must behave exactly as without sanitization."""
    return get_pii_config().get_level(PII_SINK_LOGS) != PiiLevel.NONE


def sanitize_record(
    record: logging.LogRecord,
    format_exception: Callable[[Any], Any],
    exception_attributes: bool = False,
    level: Optional[PiiLevel] = None,
    text_level: Optional[PiiLevel] = None,
) -> logging.LogRecord:
    """Return a copy of ``record`` sanitized at the ``logs`` PII level. Never raises.

    ``level`` replaces the ``logs`` level; ``text_level`` replaces it for free text only.
    ``record`` itself is returned at ``NONE``.
    """
    try:
        if level is None:
            level = get_pii_config().get_level(PII_SINK_LOGS)
        if level == PiiLevel.NONE:
            return record
        if getattr(_state, "active", False):
            # Something rendered by the sanitizer logged: don't recurse into it.
            return redacted_record(record)
        _state.active = True
        try:
            scrubber = _Scrubber(level, text_level)
            return _sanitize(record, scrubber, format_exception, exception_attributes)
        finally:
            _state.active = False
    except Exception:  # noqa: BLE001 - never raise from inside logging, never emit raw
        return redacted_record(record)


def sanitize_template_and_args(
    record: logging.LogRecord,
    clean: logging.LogRecord,
    template: Any,
    level: PiiLevel,
    text_level: PiiLevel,
) -> Tuple[str, Any]:
    """``(template, args)`` to send beside ``clean``, the sanitized copy of ``record``.

    The ``%`` template is kept (it is what Sentry groups by), scrubbed as text. The
    arguments are returned only when sanitizing changed neither them nor the formatted
    message, else ``None``: a secret can be split between template and argument
    (``"token=%s"``), so no single argument can be cleaned on its own. Never raises.
    """
    message = clean.getMessage()
    if not record.args or message == _PLACEHOLDER or getattr(_state, "active", False):
        return message, None
    _state.active = True
    try:
        scrubber = _Scrubber(level, text_level)
        if isinstance(record.msg, str) and isinstance(template, str):
            template = scrubber.text(template)
        else:
            template = message
        # Judged at the full ``level``: ``text_level`` leaves hashing to a later pass,
        # which cannot see inside an argument. ``record.message`` is the raw formatted
        # text the SDK's handler cached.
        strict = _Scrubber(level)
        raw = record.__dict__.get("message")
        if message != raw or strict.text(message) != raw:
            return template, None
        args = _plain(record.args, strict.walk)
        unchanged = not strict.walk.hidden and strict.leaves(strict._rules(args)) == args
        return template, args if unchanged else None
    except Exception:  # noqa: BLE001 - never raise from inside logging, never emit raw
        return message, None
    finally:
        _state.active = False
