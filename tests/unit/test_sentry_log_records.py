"""What Sentry derives from a log record is cleaned like the console output.

Goes through ``init_sentry()`` and the real SDK logging integration, and asserts
on the serialized envelopes.
"""

import copy
import hashlib
import json
import logging
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest
import sentry_sdk
from django.http import QueryDict
from django.test import RequestFactory, override_settings
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from observe_kit import log_sanitize
from observe_kit.pii_rules import PiiConfig, PiiLevel, _hash_value, get_pii_config, set_pii_config
from observe_kit.sentry import config

# Assembled at runtime: the SDK also sends the source lines around each frame.
AUTH, COOKIE, APIKEY, NESTED, QDPASS, QDTOKEN, QUERY, SPLIT = SECRETS = tuple(
    "zzq" + name
    for name in ("auth", "cookie", "apikey", "nested", "qdpass", "qdtoken", "query", "split")
)
EMAIL = "@".join(["somchai", "example.com"])
NAME = "tests.sentry_log_records"
LOGGER = logging.getLogger(NAME)
DJANGO_LOGGER = "django.security.csrf"
Event = Dict[str, Any]


class _Capture(sentry_sdk.transport.Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: List[str] = []
        self.events: List[Event] = []

    def capture_envelope(self, envelope: Any) -> None:
        self.envelopes.append(envelope.serialize().decode())
        event = envelope.get_event() or envelope.get_transaction_event()
        if event is not None:
            self.events.append(json.loads(json.dumps(event)))


class Sentry:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.transport = _Capture()
        self.monkeypatch = monkeypatch

    def init(self, level: Optional[PiiLevel] = None, **options: Any) -> None:
        real_init = sentry_sdk.init

        def _init(**kwargs: Any) -> Any:
            # The Django integration patches globally; the logging one is under test.
            kwargs["integrations"] = [
                i for i in kwargs["integrations"] if not isinstance(i, DjangoIntegration)
            ] + [LoggingIntegration()]
            return real_init(
                transport=self.transport,
                default_integrations=False,
                auto_enabling_integrations=False,
                **kwargs,
            )

        self.monkeypatch.setattr(config.sentry_sdk, "init", _init)
        try:
            config.init_sentry(
                "https://k@sentry.invalid/1",
                "test",
                traces_sample_rate=1.0,
                pii_level=level,
                **options,
            )
        finally:
            self.monkeypatch.setattr(config.sentry_sdk, "init", real_init)

    def send(self) -> Event:
        """Capture one error event (carrying the breadcrumbs) and return it."""
        before = len(self.transport.events)
        sentry_sdk.capture_message("flush crumbs")
        (event,) = self.transport.events[before:]
        return event

    def crumbs(self) -> List[Dict[str, Any]]:
        return self.send()["breadcrumbs"]["values"]

    @property
    def payload(self) -> str:
        return "".join(self.transport.envelopes)


@pytest.fixture
def sentry(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sentry]:
    original_config = get_pii_config()
    original_level = LOGGER.level
    set_pii_config(PiiConfig())
    LOGGER.setLevel(logging.INFO)
    django_logger = logging.getLogger(DJANGO_LOGGER)
    django_level = django_logger.level
    django_logger.setLevel(logging.INFO)
    with sentry_sdk.isolation_scope():
        sentry_sdk.get_isolation_scope().clear_breadcrumbs()
        try:
            yield Sentry(monkeypatch)
        finally:
            sentry_sdk.init()  # detach the capture client
            sentry_sdk.get_isolation_scope().clear_breadcrumbs()
            LOGGER.setLevel(original_level)
            django_logger.setLevel(django_level)
            set_pii_config(original_config)


def _request() -> Any:
    return RequestFactory().get(
        f"/cb?token={QUERY}",
        HTTP_AUTHORIZATION=f"Bearer {AUTH}",
        HTTP_COOKIE=f"sid={COOKIE}",
        HTTP_X_API_KEY=APIKEY,
    )


def _meta(log: Callable[..., None]) -> None:
    log("meta", extra={"meta": _request().META})


def _header_dump(log: Callable[..., None]) -> None:
    log("headers %s", dict(_request().headers))


def _nested_extra(log: Callable[..., None]) -> None:
    log("nested", extra={"ctx": {"a": [{"password": NESTED}]}})


def _querydict(log: Callable[..., None]) -> None:
    log("form", extra={"form": QueryDict(f"password={QDPASS}&token={QDTOKEN}")})


def _querydict_arg(log: Callable[..., None]) -> None:
    log("form %s", QueryDict(f"password={QDPASS}&token={QDTOKEN}"))


def _split_secret(log: Callable[..., None]) -> None:
    log("GET /cb?access_token=%s", SPLIT)


def _mapping_args(log: Callable[..., None]) -> None:
    log("user %(id)s", {"id": 7, "password": NESTED})  # an argument the text never shows


def _dict_message(log: Callable[..., None]) -> None:
    log({"password": NESTED, "who": EMAIL})  # type: ignore[arg-type]


def _preformatted(log: Callable[..., None]) -> None:
    log(f"mail {EMAIL} at https://h.test/p?token={QUERY}")


def _django_request(log: Callable[..., None]) -> None:
    log("Internal Server Error: %s", "/cb", extra={"request": _request(), "status_code": 500})


SHAPES = [
    _meta,
    _header_dump,
    _nested_extra,
    _querydict,
    _querydict_arg,
    _split_secret,
    _mapping_args,
    _dict_message,
    _preformatted,
    _django_request,
]


def _assert_clean(text: str) -> None:
    for raw in (*SECRETS, EMAIL):
        assert raw not in text, f"{raw!r} reached Sentry: {text}"


# Not ``django.request``: the SDK's Django integration ignores that logger.
@pytest.mark.parametrize("name", [NAME, DJANGO_LOGGER])
@pytest.mark.parametrize("shape", SHAPES)
def test_breadcrumbs_are_clean(sentry: Sentry, shape: Any, name: str) -> None:
    sentry.init()

    shape(logging.getLogger(name).info)
    crumbs = [c for c in sentry.crumbs() if c.get("category") == name]

    assert len(crumbs) == 1
    assert crumbs[0]["type"] == "log"
    assert crumbs[0]["level"] == "info"
    _assert_clean(sentry.payload)


def _error(log: logging.Logger) -> Callable[..., None]:
    return log.error


def _exception(log: logging.Logger) -> Callable[..., None]:
    def call(*args: Any, **kwargs: Any) -> None:
        try:
            raise ValueError(f"boom https://h.test/?token={QUERY}")
        except ValueError:
            log.exception(*args, **kwargs)

    return call


def _stack_info(log: logging.Logger) -> Callable[..., None]:
    return lambda *args, **kwargs: log.error(*args, stack_info=True, **kwargs)


@pytest.mark.parametrize("kind", [_error, _exception, _stack_info])
@pytest.mark.parametrize("shape", SHAPES)
def test_events_created_from_log_records_are_clean(sentry: Sentry, shape: Any, kind: Any) -> None:
    sentry.init()

    shape(kind(LOGGER))

    (event,) = sentry.transport.events
    assert event["logger"] == NAME
    assert event["level"] == "error"
    _assert_clean(sentry.payload)
    # The same record is also the breadcrumb on whatever is sent next.
    sentry.send()
    _assert_clean(sentry.payload)


def test_exception_events_keep_the_stack_trace(sentry: Sentry) -> None:
    sentry.init()

    _exception(LOGGER)("failed")

    (value,) = sentry.transport.events[0]["exception"]["values"]
    assert value["type"] == "ValueError"
    assert value["stacktrace"]["frames"]


def test_clean_records_keep_template_params_and_extra(sentry: Sentry) -> None:
    sentry.init()

    LOGGER.error("order %s failed after %d tries", "A1", 3, extra={"order": {"id": 7}})

    event = sentry.transport.events[0]
    assert event["logentry"] == {
        "message": "order %s failed after %d tries",
        "formatted": "order A1 failed after 3 tries",
        "params": ["A1", 3],
    }
    assert event["extra"]["order"] == {"id": 7}


def test_redacted_records_keep_the_template_and_drop_params(sentry: Sentry) -> None:
    sentry.init()

    LOGGER.error("login %s as %s", EMAIL, "admin")

    logentry = sentry.transport.events[0]["logentry"]
    assert logentry == {"message": "login %s as %s", "formatted": "login s***@example.com as admin"}


def test_application_objects_are_not_mutated(sentry: Sentry) -> None:
    sentry.init()
    extra = {"ctx": {"password": NESTED, "who": EMAIL}}
    args = {"password": APIKEY}
    snapshot = copy.deepcopy((extra, args))
    seen: List[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = seen.append  # type: ignore[method-assign]
    LOGGER.addHandler(handler)
    try:
        LOGGER.error("x %s", args, extra=extra)
        sentry.send()
    finally:
        LOGGER.removeHandler(handler)

    assert (extra, args) == snapshot
    (record,) = seen
    assert record.ctx is extra["ctx"]  # type: ignore[attr-defined]
    assert record.args is args
    _assert_clean(sentry.payload)


def test_before_send_sees_cleaned_log_data_and_the_raw_record(sentry: Sentry) -> None:
    seen: List[Any] = []

    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        seen.append((repr(event), hint.get("log_record")))
        event.setdefault("extra", {})["added"] = {"by": EMAIL}
        return event

    sentry.init(before_send=hook)

    _nested_extra(LOGGER.info)
    LOGGER.error("login %s", EMAIL, extra={"ctx": {"password": NESTED}})

    ((text, record),) = seen
    _assert_clean(text)
    assert record.args == (EMAIL,)
    _assert_clean(sentry.payload)  # including what the hook added
    assert sentry.transport.events[0]["extra"]["added"] == {"by": "s***@example.com"}


def test_before_send_changes_to_log_fields_are_kept(sentry: Sentry) -> None:
    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        del event["extra"]["ctx"]
        event["logentry"]["message"] = "rewritten"
        event["fingerprint"] = ["mine"]
        return event

    sentry.init(before_send=hook)

    LOGGER.error("login %s", "bob", extra={"ctx": {"a": 1}})

    event = sentry.transport.events[0]
    assert "ctx" not in event["extra"]
    assert event["logentry"]["message"] == "rewritten"
    assert event["fingerprint"] == ["mine"]


def test_sdk_event_scrubber_still_applies_to_log_extra(sentry: Sentry) -> None:
    sentry.init()

    LOGGER.error("x", extra={"csrftoken": SPLIT})

    assert sentry.transport.events[0]["extra"]["csrftoken"] == "[Filtered]"
    _assert_clean(sentry.payload)


def test_scope_data_and_manual_breadcrumbs_are_untouched(sentry: Sentry) -> None:
    sentry.init()
    sentry_sdk.add_breadcrumb(category="manual", message="kept", data={"n": 1})

    with sentry_sdk.new_scope() as scope:
        scope.set_extra("from_scope", "kept")
        LOGGER.error("x")

    event = sentry.transport.events[0]
    assert event["extra"]["from_scope"] == "kept"
    (crumb,) = [c for c in event["breadcrumbs"]["values"] if c.get("category") == "manual"]
    assert (crumb["message"], crumb["data"]) == ("kept", {"n": 1})


def test_breadcrumbs_are_clean_on_transactions(sentry: Sentry) -> None:
    sentry.init()

    with sentry_sdk.start_transaction(name="job", op="task"):
        for shape in SHAPES:
            shape(LOGGER.info)
        _error(LOGGER)("failed", extra={"ctx": {"password": NESTED}})

    _assert_clean(sentry.payload)


@pytest.mark.parametrize(
    "logs,sink",
    [
        (PiiLevel.NONE, PiiLevel.BASIC),
        (PiiLevel.BASIC, PiiLevel.BASIC),
        (PiiLevel.SENSITIVE, PiiLevel.BASIC),
        (PiiLevel.BASIC, PiiLevel.SENSITIVE),
        (PiiLevel.SENSITIVE, PiiLevel.SENSITIVE),
        (PiiLevel.NONE, PiiLevel.SENSITIVE),
    ],
)
def test_the_stricter_level_applies_and_values_are_hashed_once(
    sentry: Sentry, logs: PiiLevel, sink: PiiLevel
) -> None:
    set_pii_config(PiiConfig({"logs": logs.value}))
    values = {
        key: "zzh" + key
        for key in ("meta_ua", "ua", "operator", "in_url", "in_data_url", "in_query", "in_key")
    }
    with override_settings(
        OBSERVE_KIT={"EXTRA_HASH_FIELDS": ["session_id"], "PII_HASH_SALT": "salt"}
    ):
        sentry.init(sink)
        extra = {
            "meta": {"HTTP_USER_AGENT": values["meta_ua"], "QUERY_STRING": "x=1"},
            "user-agent": values["ua"],
            "ctx": {"session_id": values["operator"]},
            "note": f"see https://h.test/p?session_id={values['in_data_url']} now",
            "req": {"QUERY_STRING": f"session_id={values['in_query']}"},
            "password": NESTED,
            # A URL in a dict key: only the log pass scrubs those.
            "seen": {f"/p?session_id={values['in_key']}": 1},
        }
        LOGGER.error("open https://h.test/p?session_id=%s", values["in_url"], extra=extra)
        event = sentry.send()

    _assert_clean(sentry.payload)  # credentials, at every combination
    hashed = PiiLevel.SENSITIVE in (logs, sink)
    sent = json.dumps(sentry.transport.events)
    for name, raw in values.items():
        digest = hashlib.sha256(("salt" + raw).encode()).hexdigest()
        double = hashlib.sha256(("salt" + digest).encode()).hexdigest()
        assert double not in sent, name
        if hashed:
            assert raw not in sent, name
            assert digest in sent, name
    # Every copy of one record agrees: the event, its breadcrumb, and the next event's.
    first = sentry.transport.events[0]
    (crumb,) = [c for c in event["breadcrumbs"]["values"] if c.get("category") == NAME]
    for key in ("meta", "user-agent", "ctx", "note", "req"):
        assert crumb["data"].get(key) == first["extra"].get(key), key
    assert crumb["message"] == first["logentry"]["formatted"]


def test_hashed_once_through_a_user_before_send(sentry: Sentry) -> None:
    with override_settings(OBSERVE_KIT={"PII_HASH_SALT": "salt"}):
        sentry.init(PiiLevel.SENSITIVE, before_send=lambda event, hint: event)
        LOGGER.error("x", extra={"user-agent": "zzhagent"})
        sentry.send()

    digest = hashlib.sha256(b"saltzzhagent").hexdigest()
    first, second = sentry.transport.events
    assert first["extra"]["user-agent"] == digest
    (crumb,) = [c for c in second["breadcrumbs"]["values"] if c.get("category") == NAME]
    assert crumb["data"]["user-agent"] == digest


def test_other_outputs_still_get_plain_string_hashes(sentry: Sentry) -> None:
    sentry.init(PiiLevel.SENSITIVE)
    record = logging.LogRecord(NAME, 20, __file__, 1, "x", None, None)
    record.__dict__["user-agent"] = "zzhagent"
    set_pii_config(PiiConfig({"logs": "SENSITIVE"}))

    clean = log_sanitize.sanitize_record(record, format_exception=str)

    # Exact type: older OpenTelemetry SDKs and some DB drivers reject subclasses.
    assert type(clean.__dict__["user-agent"]) is str
    assert type(_hash_value("a", "b")) is str


def test_sentry_level_none_opts_out(sentry: Sentry) -> None:
    sentry.init(PiiLevel.NONE)

    LOGGER.error("login %s", EMAIL, extra={"ctx": {"password": NESTED}})
    sentry.send()

    event = sentry.transport.events[0]
    assert event["logentry"]["params"] == [EMAIL]
    assert event["extra"]["ctx"] == {"password": NESTED}
    assert NESTED in json.dumps(sentry.transport.events[1]["breadcrumbs"])


class _Boom(RuntimeError):
    pass


@pytest.mark.parametrize("error", [_Boom, RecursionError, KeyboardInterrupt])
def test_a_failing_sanitizer_never_raises_and_never_sends_raw(
    sentry: Sentry, monkeypatch: pytest.MonkeyPatch, error: type
) -> None:
    sentry.init()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise error("boom")

    monkeypatch.setattr(log_sanitize, "_sanitize", explode)
    monkeypatch.setattr(log_sanitize, "redacted_record", explode)

    LOGGER.info("login %s", EMAIL, extra={"ctx": {"password": NESTED}})
    LOGGER.error("login %s", EMAIL, extra={"ctx": {"password": NESTED}})
    event = sentry.send()

    assert not [c for c in event["breadcrumbs"]["values"] if c.get("category") == NAME]
    _assert_clean(sentry.payload)


def test_a_record_that_cannot_be_formatted_is_not_sent_raw(
    sentry: Sentry, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentry.init()

    class _Broken:
        calls = 0

        def __str__(self) -> str:
            # Formats once for the SDK, then fails inside the sanitizer.
            _Broken.calls += 1
            if _Broken.calls > 2:
                raise _Boom("boom")
            return "ok"

    # Only the SDK's handler: any other one would raise from formatting the raw record.
    monkeypatch.setattr(LOGGER, "propagate", False)
    monkeypatch.setattr(LOGGER, "handlers", [logging.NullHandler()])
    LOGGER.error("v %s %s", _Broken(), EMAIL, extra={"ctx": {"password": NESTED}})
    sentry.send()

    _assert_clean(sentry.payload)


def test_objects_that_log_while_rendered_do_not_recurse(sentry: Sentry) -> None:
    sentry.init()
    renders: List[int] = []

    class _Chatty:
        def __str__(self) -> str:
            if getattr(log_sanitize._state, "active", False):
                renders.append(1)
                LOGGER.info("rendering %s", EMAIL)  # not ``self``: see above
            return EMAIL

    LOGGER.error("value %s", _Chatty(), extra={"obj": _Chatty()})
    sentry.send()

    assert 0 < len(renders) < 20
    _assert_clean(sentry.payload)
