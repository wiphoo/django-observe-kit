"""Bypasses and cost limits of application log sanitization."""

# ruff: noqa: F811 - the log fixtures are imported from the sibling test module

from __future__ import annotations

import collections
import dataclasses
import logging
import time
from enum import Enum
from types import MappingProxyType
from typing import Any

import pytest
from django.test import RequestFactory

from observe_kit.logging.config import RequestFormatter
from observe_kit.otel.config import _SafeOTelLogHandler
from observe_kit.pii_rules import PiiConfig, set_pii_config
from observe_kit.sentry import scrub_event
from observe_kit.sentry.scrub.emails import _mask_emails_in_leaves
from tests.unit.scaling import assert_scales_linearly
from tests.unit.test_log_sanitization import SECRET, Outputs, log, outputs  # noqa: F401


@dataclasses.dataclass
class Config:
    user: str
    client_secret: str = dataclasses.field(default=SECRET, repr=False)


@pytest.mark.parametrize(
    "msg,args,extra",
    [
        ("x", (), {"config": Config("u")}),
        (Config("u"), (), {}),
        ("%s", ([Config("u"), {"password": "p"}],), {}),
    ],
    ids=["extra", "msg", "arg-with-another-rule"],
)
def test_dataclass_fields_hidden_from_repr_stay_hidden(
    outputs: Outputs, log: logging.Logger, msg: Any, args: Any, extra: Any
) -> None:
    log.info(msg, *args, extra=extra)
    outputs.assert_clean(SECRET)


def test_unset_dataclass_field_does_not_blank_the_record(
    outputs: Outputs, log: logging.Logger
) -> None:
    @dataclasses.dataclass
    class Lazy:
        a: int
        late: int = dataclasses.field(init=False)

    log.info("keep", extra={"lazy": Lazy(1)})
    (line,) = outputs.json_lines()
    assert (line["message"], line["lazy"]) == ("keep", {"a": 1})


class KeyEnum(str, Enum):
    PASSWORD = "password"


class PlainKeyEnum(Enum):
    PASSWORD = "password"


class KeyObject:
    def __str__(self) -> str:
        return "password"


@pytest.mark.parametrize(
    "key",
    [b"password", KeyEnum.PASSWORD, PlainKeyEnum.PASSWORD, KeyObject()],
    ids=["bytes", "str-enum", "enum", "object"],
)
def test_non_str_keys_match_the_key_rules(outputs: Outputs, log: logging.Logger, key: Any) -> None:
    log.info("x", extra={"d": {key: SECRET}, "pairs": [(key, SECRET)]})
    log.info("%s", {key: SECRET})
    outputs.assert_clean(SECRET)
    line = outputs.json_lines()[0]
    assert line["d"] == {"password": "[Filtered]"}
    assert line["pairs"] == [["password", "[Filtered]"]]


def test_keys_that_collapse_do_not_pass_the_original_arg_through(
    outputs: Outputs, log: logging.Logger
) -> None:
    log.info("%s", {b"password": SECRET, "password": ""})
    outputs.assert_clean(SECRET)


def _meta() -> dict[str, Any]:
    meta = (
        RequestFactory()
        .get("/p", HTTP_AUTHORIZATION=f"Bearer {SECRET}", HTTP_COOKIE=f"sessionid={SECRET}")
        .META
    )
    return {**meta, "CSRF_COOKIE": SECRET, "QUERY_STRING": f"token={SECRET}&page=2"}


def test_request_meta_gets_the_header_rules(outputs: Outputs, log: logging.Logger) -> None:
    meta = _meta()
    log.info("x", extra={"meta": meta, "pairs": list(meta.items())[-2:]})
    log.info("%s", meta)
    outputs.assert_clean(SECRET)
    line = outputs.json_lines()[0]
    assert "HTTP_AUTHORIZATION" not in line["meta"]
    assert "HTTP_COOKIE" not in line["meta"]
    assert "CSRF_COOKIE" not in line["meta"]
    assert line["meta"]["QUERY_STRING"] == "token=REDACTED&page=2"
    assert line["meta"]["REQUEST_METHOD"] == "GET"


def test_request_meta_operator_rule_by_header_name(
    outputs: Outputs, log: logging.Logger, observe_kit_settings: Any
) -> None:
    with observe_kit_settings({"EXTRA_DROP_HEADERS": ["x-auth-token"]}):
        log.info("x", extra={"meta": {"HTTP_X_AUTH_TOKEN": SECRET}})
    outputs.assert_clean(SECRET)


def test_cookies_container_is_dropped(outputs: Outputs, log: logging.Logger) -> None:
    log.info("x", extra={"cookies": {"sessionid": SECRET}})
    outputs.assert_clean(SECRET)


@pytest.mark.parametrize(
    "build",
    [
        lambda: collections.deque([{"password": SECRET}]),
        lambda: {"a": {"password": SECRET}}.values(),
        lambda: {"password": SECRET}.items(),
        lambda: MappingProxyType({"password": SECRET}),
    ],
    ids=["deque", "values", "items", "mappingproxy"],
)
def test_other_containers_are_opened(outputs: Outputs, log: logging.Logger, build: Any) -> None:
    value = build()
    log.info("x", extra={"d": value})
    log.info("%s", value)
    log.info(value)
    outputs.assert_clean(SECRET)


def test_dict_message_with_positional_args(outputs: Outputs, log: logging.Logger) -> None:
    msg = {"password": SECRET, "note": "%s"}
    log.info(msg, "x")
    outputs.assert_clean(SECRET)
    assert msg == {"password": SECRET, "note": "%s"}
    assert "'note': 'x'" in outputs.json_lines()[0]["message"]


def _dense_cycle() -> Any:
    nodes: list[dict[str, Any]] = [{"password": SECRET} for _ in range(10)]
    for node in nodes:
        for i, other in enumerate(nodes):
            node[str(i)] = other
    return nodes[0]


def _shared_refs() -> Any:
    node: Any = {"password": SECRET}
    for _ in range(20):
        node = [node, node]
    return node


@pytest.mark.parametrize("build", [_dense_cycle, _shared_refs], ids=["dense-cycle", "shared-refs"])
def test_exponential_structures_are_cut_off(outputs: Outputs, build: Any) -> None:
    value = build()
    # Handed to the library's handlers only: pytest's capture handlers ``repr`` the raw
    # arguments, which is exponential in itself for a dense cycle.
    handlers = [h for h in logging.getLogger().handlers if _is_library_handler(h)]
    assert len(handlers) == 2
    fields = {"name": "app.big", "levelname": "INFO", "levelno": 20}
    records = [
        logging.makeLogRecord({**fields, "msg": "data %s", "args": (value,), "d": value}),
        logging.makeLogRecord({**fields, "msg": value}),
    ]
    started = time.process_time()
    for record in records:
        for handler in handlers:
            handler.handle(record)
    # Unbounded, either structure takes over 10 s of CPU.
    assert time.process_time() - started < 5.0
    outputs.assert_clean(SECRET)


def _is_library_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, _SafeOTelLogHandler) or isinstance(
        handler.formatter, RequestFormatter
    )


@pytest.mark.parametrize(
    "build",
    [
        # Cutting a pair list short must not leave earlier pairs to be read positionally.
        lambda: [("password", SECRET)] + [("k", n) for n in range(60_000)],
        lambda: [("password", SECRET), ("k", list(range(60_000)))],
        lambda: {"a": list(range(60_000)), "password": SECRET},
        lambda: [{"password": SECRET}] * 60_000,
    ],
    ids=["long-pair-list", "pair-with-huge-value", "wide-dict", "wide-list"],
)
def test_over_budget_data_is_filtered_never_raw(
    outputs: Outputs, log: logging.Logger, build: Any
) -> None:
    log.info("data %s", build(), extra={"d": build()})
    outputs.assert_clean(SECRET)


def test_over_budget_extra_keeps_the_other_record_fields(
    outputs: Outputs, log: logging.Logger
) -> None:
    log.info("keep", extra={"big": list(range(60_000)), "after": "kept", "n": 3})
    (line,) = outputs.json_lines()
    assert line["big"] == "[Filtered]"
    assert (line["message"], line["after"], line["n"]) == ("keep", "kept", 3)
    assert "trace_id" in line  # added by RequestContextFilter after the caller's extra


def test_ordinary_large_data_is_within_budget(outputs: Outputs, log: logging.Logger) -> None:
    rows = [{"id": n, "name": "row"} for n in range(2000)]
    log.info("x", extra={"rows": rows})
    assert outputs.json_lines()[0]["rows"] == rows


def _colliding_keys(n: int) -> dict[str, int]:
    return {f"user{i}@example.com": i for i in range(n)}


def test_colliding_masked_log_keys_scale_linearly(outputs: Outputs, log: logging.Logger) -> None:
    assert_scales_linearly(_colliding_keys, lambda d: log.info("x", extra={"d": d}), 6000)


def test_colliding_masked_sentry_keys_scale_linearly() -> None:
    assert_scales_linearly(_colliding_keys, lambda d: scrub_event({"extra": {"d": d}}), 16000)


@pytest.mark.parametrize(
    "work",
    [
        lambda d: scrub_event({"extra": {"d": d}})["extra"]["d"],
        lambda d: scrub_event({"tags": d})["tags"],
        lambda d: _mask_emails_in_leaves(d),
    ],
    ids=["fused-walk", "named-entries", "mask-leaves"],
)
def test_colliding_key_suffixes_are_unchanged(work: Any) -> None:
    # A literal key that already looks suffixed is still skipped over.
    data = {"u***@x.com#3": 0, "u1@x.com": 1, "u2@x.com": 2, "u3@x.com": 3, "u4@x.com": 4}
    assert list(work(data)) == [
        "u***@x.com#3",
        "u***@x.com",
        "u***@x.com#2",
        "u***@x.com#4",
        "u***@x.com#5",
    ]


def test_none_level_bad_format_behaves_as_without_sanitization(outputs: Outputs) -> None:
    set_pii_config(PiiConfig(levels={"logs": "NONE"}))
    record = logging.makeLogRecord(
        {"name": "app.bad", "levelname": "INFO", "levelno": 20, "msg": "%d", "args": ("x",)}
    )
    (otel_handler,) = [
        h for h in logging.getLogger().handlers if isinstance(h, _SafeOTelLogHandler)
    ]
    # The SDK handler lets the formatting error through; nothing is exported.
    with pytest.raises(TypeError):
        otel_handler.handle(record)
    assert outputs.otel_records() == []
