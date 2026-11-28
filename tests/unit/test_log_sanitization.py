"""Application log records must be sanitized on every output this library configures."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import io
import json
import logging
import time
from collections import namedtuple
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.http import QueryDict
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor

from observe_kit.logging.config import RequestFormatter, configure_logging
from observe_kit.otel.config import _ensure_otel_logging_handler, _SafeOTelLogHandler
from observe_kit.pii_rules import PiiConfig, get_pii_config, set_pii_config

EMAIL = "alice@example.com"
SECRET = "s3cr3t-value"
PHONE = "0812345678"
LEAKS = (EMAIL, SECRET, PHONE)


class Outputs:
    """The two outputs the library configures: JSON console and OTel log export."""

    def __init__(self, stream: io.StringIO, exporter: InMemoryLogRecordExporter) -> None:
        self.stream = stream
        self.exporter = exporter

    def json_lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def otel_records(self) -> list[Any]:
        return [item.log_record for item in self.exporter.get_finished_logs()]

    def texts(self) -> dict[str, str]:
        otel = " ".join(f"{r.body!r} {dict(r.attributes)!r}" for r in self.otel_records())
        return {"console": self.stream.getvalue(), "otel": otel}

    def assert_clean(self, *leaks: str) -> None:
        for name, text in self.texts().items():
            assert text, f"nothing reached {name}"
            for leak in leaks or LEAKS:
                assert leak not in text, f"{leak!r} leaked to {name}: {text}"


@pytest.fixture
def outputs() -> Iterator[Outputs]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    original = get_pii_config()
    set_pii_config(PiiConfig())
    root.handlers = []
    configure_logging(level="INFO")
    stream = io.StringIO()
    root.handlers[0].setStream(stream)  # type: ignore[attr-defined]
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    _ensure_otel_logging_handler(provider)
    yield Outputs(stream, exporter)
    root.handlers, root.level = handlers, level
    set_pii_config(original)


@pytest.fixture
def log() -> logging.Logger:
    return logging.getLogger("app.test_log_sanitization")


def test_extra_fields_get_credential_redaction_and_field_rules(
    outputs: Outputs, log: logging.Logger
) -> None:
    extra = {
        "email": EMAIL,
        "phone": PHONE,
        "password": SECRET,
        "ctx": {"API_KEY": SECRET, "pairs": [("token", SECRET)], "note": f"mail {EMAIL}"},
        "authorization": f"Bearer {SECRET}",
        "_private": {"secret": SECRET},
        "count": 3,
    }
    original = copy.deepcopy(extra)

    log.info("hello", extra=extra)

    outputs.assert_clean()
    (line,) = outputs.json_lines()
    assert line["password"] == "[Filtered]"
    assert line["email"] == "a***@example.com"
    assert line["count"] == 3
    assert "authorization" not in line
    assert line["message"] == "hello"
    assert line["logger"] == "app.test_log_sanitization"
    assert extra == original


def test_log_request_complete_shape_nested_extra(outputs: Outputs, log: logging.Logger) -> None:
    log.info("request_complete", extra={"extra": {"event": "request_complete", "token": SECRET}})
    outputs.assert_clean()
    assert outputs.json_lines()[0]["extra"]["token"] == "[Filtered]"


@pytest.mark.parametrize(
    "msg,args",
    [
        (f"login {EMAIL} https://h/cb?token={SECRET}&page=2", ()),
        ("login %s %s", (EMAIL, f"https://h/cb?password={SECRET}")),
        ("GET /cb?access_token=%s from %s", (SECRET, EMAIL)),
        ("%(who)s sent %(password)s", ({"who": EMAIL, "password": SECRET},)),
        ("payload %s", ({"password": SECRET, "email": EMAIL},)),
        ("payloads %s %d", ([{"api_key": SECRET}], 3)),
    ],
)
def test_message_and_args(
    outputs: Outputs, log: logging.Logger, msg: str, args: tuple[Any, ...]
) -> None:
    snapshot = copy.deepcopy(args)
    log.info(msg, *args)
    outputs.assert_clean()
    assert args == snapshot


def test_args_formatting_is_preserved(outputs: Outputs, log: logging.Logger) -> None:
    log.info("%s took %.2f s (%d%%) %r", "job", 1.5, 90, None)
    assert outputs.json_lines()[0]["message"] == "job took 1.50 s (90%) None"
    assert outputs.otel_records()[0].body == "job took 1.50 s (90%) None"


def test_dict_message(outputs: Outputs, log: logging.Logger) -> None:
    msg = {"password": SECRET, "user": EMAIL}
    log.info(msg)
    outputs.assert_clean()
    assert msg == {"password": SECRET, "user": EMAIL}
    assert outputs.json_lines()[0]["password"] == "[Filtered]"


def test_exception_text_and_stack_info(outputs: Outputs, log: logging.Logger) -> None:
    try:
        raise ValueError(f"bad {EMAIL} at https://h/?token={SECRET}")
    except ValueError as exc:
        log.exception("boom %s", exc, stack_info=True)
    outputs.assert_clean(EMAIL, SECRET)
    (line,) = outputs.json_lines()
    assert "ValueError: bad a***@example.com" in line["exc_info"]
    assert line["stack_info"].startswith("Stack (most recent call last)")
    attributes = outputs.otel_records()[0].attributes
    assert attributes["exception.type"] == "ValueError"
    assert "a***@example.com" in attributes["exception.message"]
    assert "Traceback" in attributes["exception.stacktrace"]


def test_cached_exc_text_from_an_earlier_formatter_is_sanitized(outputs: Outputs) -> None:
    record = logging.makeLogRecord(
        {"msg": "boom", "levelno": logging.ERROR, "exc_text": f"ValueError: {EMAIL}"}
    )
    record.message = f"stale {EMAIL}"
    logging.getLogger().handle(record)
    outputs.assert_clean(EMAIL)


def test_objects_are_sanitized(outputs: Outputs, log: logging.Logger) -> None:
    @dataclasses.dataclass
    class Login:
        user: str
        password: str

    Row = namedtuple("Row", "user password")

    class Request:
        def __repr__(self) -> str:
            return f"<WSGIRequest: GET '/cb?token={SECRET}&email={EMAIL}'>"

    log.warning(
        "objects",
        extra={
            "login": Login(EMAIL, SECRET),
            "row": Row(EMAIL, SECRET),
            "request": Request(),
            "err": ValueError(EMAIL),
            "raw": f"to {EMAIL}".encode(),
            "tags": {EMAIL},
        },
    )
    log.error(ValueError(EMAIL))
    outputs.assert_clean()


def test_original_record_is_untouched_for_other_handlers(
    outputs: Outputs, log: logging.Logger
) -> None:
    seen: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    logging.getLogger().addHandler(Collect())
    payload = {"token": SECRET}
    log.info("to %s", EMAIL, extra={"password": SECRET, "payload": payload})

    (record,) = seen
    assert record.msg == "to %s"
    assert record.args == (EMAIL,)
    assert record.password == SECRET  # type: ignore[attr-defined]
    assert record.payload is payload  # type: ignore[attr-defined]
    outputs.assert_clean()


def test_hash_is_applied_once_on_every_output(
    outputs: Outputs, log: logging.Logger, observe_kit_settings: Any
) -> None:
    set_pii_config(PiiConfig(levels={"logs": "SENSITIVE"}))
    expected = hashlib.sha256(b"salt10.0.0.1").hexdigest()
    with observe_kit_settings({"PII_HASH_SALT": "salt"}):
        log.info("x", extra={"ip": "10.0.0.1"})
    assert outputs.json_lines()[0]["ip"] == expected
    assert outputs.otel_records()[0].attributes["ip"] == expected


def test_operator_rules_win(
    outputs: Outputs, log: logging.Logger, observe_kit_settings: Any
) -> None:
    with observe_kit_settings({"EXTRA_DROP_HEADERS": ["token"], "EXTRA_MASK_FIELDS": ["password"]}):
        log.info("x", extra={"token": SECRET, "password": SECRET})
    (line,) = outputs.json_lines()
    assert "token" not in line
    assert line["password"] == "s3***"


def test_inactive_hash_rule_does_not_disable_credential_redaction(
    outputs: Outputs, log: logging.Logger, observe_kit_settings: Any
) -> None:
    # Hash rules are only active at SENSITIVE; at BASIC the default redaction applies.
    with observe_kit_settings({"EXTRA_HASH_FIELDS": ["password", "token"]}):
        log.info("%(password)s https://h/cb?token=%(password)s", {"password": SECRET})
        log.info(
            "x",
            extra={"password": SECRET, "nested": {"token": SECRET}, "p": [["password", SECRET]]},
        )
    outputs.assert_clean()
    line = outputs.json_lines()[1]
    assert line["password"] == line["nested"]["token"] == "[Filtered]"
    assert line["p"] == [["password", "[Filtered]"]]


def test_none_level_leaves_records_as_given(outputs: Outputs, log: logging.Logger) -> None:
    set_pii_config(PiiConfig(levels={"logs": "NONE"}))
    log.info("to %s", EMAIL, extra={"password": SECRET})
    for text in outputs.texts().values():
        assert EMAIL in text and SECRET in text


@pytest.mark.parametrize("target", ["sanitize_body", "_scrub_text"])
def test_sanitizer_failure_never_emits_raw_and_never_raises(
    outputs: Outputs, log: logging.Logger, target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(f"observe_kit.log_sanitize.{target}", side_effect=RuntimeError(SECRET)):
        log.info("to %s?x", EMAIL, extra={"password": SECRET})
    outputs.assert_clean()
    assert "[Filtered]" in outputs.json_lines()[0]["message"]
    assert outputs.json_lines()[0]["logger"] == "app.test_log_sanitization"
    captured = capsys.readouterr()
    assert EMAIL not in captured.err and SECRET not in captured.err


def test_bad_format_args_do_not_dump_raw_values(
    outputs: Outputs, capsys: pytest.CaptureFixture[str]
) -> None:
    # Handed to the library's handlers directly: pytest's capture handlers would
    # report the bad format themselves.
    record = logging.makeLogRecord(
        {
            "name": "app.bad",
            "levelname": "INFO",
            "levelno": 20,
            "msg": "%d " + EMAIL,
            "args": (SECRET,),
        }
    )
    handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, _SafeOTelLogHandler) or isinstance(h.formatter, RequestFormatter)
    ]
    assert len(handlers) == 2
    for handler in handlers:
        handler.handle(record)
    outputs.assert_clean()
    captured = capsys.readouterr()
    assert EMAIL not in captured.err and SECRET not in captured.err


def test_serialization_failure_does_not_dump_raw_values(
    outputs: Outputs, log: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    formatter = logging.getLogger().handlers[0].formatter
    assert formatter is not None
    real = formatter.serialize_log_record  # type: ignore[attr-defined]
    calls: list[int] = []

    def flaky(data: Any) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise TypeError("nope")
        return str(real(data))

    with patch.object(formatter, "serialize_log_record", side_effect=flaky):
        log.info("to %s", EMAIL)
    outputs.assert_clean()
    assert EMAIL not in capsys.readouterr().err


def test_hostile_values_do_not_raise_or_recurse(outputs: Outputs, log: logging.Logger) -> None:

    class Broken:
        def __str__(self) -> str:
            raise RuntimeError(SECRET)

        __repr__ = __str__

    class Chatty:
        def __str__(self) -> str:
            # In ``extra``, so only the sanitizer renders it.
            log.info("rendering", extra={"obj": Chatty()})
            return EMAIL

        __repr__ = __str__

    cycle: dict[str, Any] = {"password": SECRET}
    cycle["a"] = cycle
    cycle["b"] = [cycle, cycle]

    log.info("broken", extra={"obj": Broken(), "cycle": cycle, "keys": {(1, 2): EMAIL}})
    log.info("chatty", extra={"obj": Chatty()})
    outputs.assert_clean()
    messages = [line["message"] for line in outputs.json_lines()]
    assert "broken" in messages and "chatty" in messages
    assert len(messages) < 20


@pytest.mark.parametrize("chunk", [f"{EMAIL} ", "word ", "https://h/p?token=x ", "a?b ", "%40"])
def test_large_message_stays_fast(outputs: Outputs, log: logging.Logger, chunk: str) -> None:
    message = chunk * (200_000 // len(chunk))
    started = time.perf_counter()
    log.info(message, extra={"blob": message})
    # Four 200 KB scrubs (message and extra, two outputs). A quadratic pass takes minutes.
    assert time.perf_counter() - started < 20.0
    assert EMAIL not in outputs.stream.getvalue()


def test_exception_as_array_formatter_option(outputs: Outputs, log: logging.Logger) -> None:
    formatter = logging.getLogger().handlers[0].formatter
    with patch.object(formatter, "exc_info_as_array", True, create=True):
        try:
            raise ValueError(f"bad {EMAIL}")
        except ValueError:
            log.exception("boom")
    outputs.assert_clean(EMAIL)
    assert isinstance(outputs.json_lines()[0]["exc_info"], list)


def _too_deep() -> Any:
    node: Any = {"password": SECRET}
    for _ in range(80):
        node = [node]
    return node


@pytest.mark.parametrize(
    "arg",
    [
        # ``.items()`` shows only the last value; the repr shows all of them.
        QueryDict(f"password={SECRET}&password="),
        [QueryDict(f"password={SECRET}&password=")],
        _too_deep(),
    ],
    ids=["querydict", "nested-querydict", "beyond-depth-cap"],
)
def test_arg_with_values_the_sanitizer_cannot_see_is_not_passed_through(
    outputs: Outputs, log: logging.Logger, arg: Any
) -> None:
    log.info("data %s", arg)
    outputs.assert_clean(SECRET)


def test_querydict_extra_and_msg(outputs: Outputs, log: logging.Logger) -> None:
    query = QueryDict(f"password={SECRET}&email={EMAIL}&n=1")
    log.info("post", extra={"post": query})
    log.info(query)
    outputs.assert_clean()
    first, second = outputs.json_lines()
    expected = {"password": "[Filtered]", "email": "a***@example.com", "n": "1"}
    assert first["post"] == expected
    assert {k: second[k] for k in expected} == expected
