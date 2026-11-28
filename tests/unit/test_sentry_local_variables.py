# ruff: noqa: F841  (unused locals are the data under test)
"""Stack-frame local variables are not sent unless the caller opts in.

Goes through ``init_sentry()`` and the real SDK pipeline, and asserts on the
serialized envelopes. The sample values are assembled at runtime because the
SDK also sends the source lines around each frame.
"""

import copy
import json
import logging
import threading
from typing import Any, Callable, Dict, Iterator, List, Tuple

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from observe_kit.sentry import config

NAME = " ".join(["Somchai", "Jaidee"])
PHONE = "08" + "12345678"
EMAIL = "@".join(["somchai", "example.com"])
SECRETS = tuple("zzq" + suffix for suffix in ("pass", "token", "client"))
LOGGER = logging.getLogger("tests.sentry_local_variables")


def _fail() -> None:
    full_name, mobile = NAME, PHONE
    payload = {"auth": {"password": SECRETS[0], "token": SECRETS[1]}}
    client_secret = SECRETS[2]
    holder = {"name": NAME, "email": EMAIL}
    raise ValueError("boom")


def _exception() -> None:
    try:
        _fail()
    except ValueError:
        sentry_sdk.capture_exception()


def _chained_exception() -> None:
    try:
        try:
            _fail()
        except ValueError as exc:
            holder = {"name": NAME, "email": EMAIL}
            raise RuntimeError("wrapped") from exc
    except RuntimeError:
        sentry_sdk.capture_exception()


def _message_with_stacktrace() -> None:
    holder = {"name": NAME, "email": EMAIL}
    sentry_sdk.capture_message("hello")


def _logged_exception() -> None:
    try:
        _fail()
    except ValueError:
        LOGGER.exception("failed")


def _logged_error_with_stacktrace() -> None:
    holder = {"name": NAME, "email": EMAIL}
    LOGGER.error("failed")


def _logged_stack_info() -> None:
    holder = {"name": NAME, "email": EMAIL}
    LOGGER.error("failed", stack_info=True)


def _thread_exception() -> None:
    thread = threading.Thread(target=_fail)
    thread.start()
    thread.join()


def _transaction() -> None:
    holder = {"name": NAME, "email": EMAIL}
    with sentry_sdk.start_transaction(name="job", op="task"):
        with sentry_sdk.start_span(op="task", name="step"):
            _exception()


PATHS = [
    _exception,
    _chained_exception,
    _message_with_stacktrace,
    _logged_exception,
    _logged_error_with_stacktrace,
    _logged_stack_info,
    _thread_exception,
    _transaction,
]


def _run(
    monkeypatch: pytest.MonkeyPatch, trigger: Callable[[], None], **options: Any
) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (everything sent, the events the user's ``before_send`` was given)."""
    sent: List[str] = []
    seen_by_hook: List[Dict[str, Any]] = []
    real_init = sentry_sdk.init

    class _Capture(sentry_sdk.transport.Transport):
        def capture_envelope(self, envelope: Any) -> None:
            sent.append(envelope.serialize().decode())

    def _init(**kwargs: Any) -> Any:
        # Swap the Django integration (patches globally) for the ones under test.
        kwargs["integrations"] = [LoggingIntegration(), ThreadingIntegration()]
        return real_init(
            transport=_Capture(),
            default_integrations=False,
            auto_enabling_integrations=False,
            attach_stacktrace=True,
            **kwargs,
        )

    def _hook(event: Dict[str, Any], hint: Any) -> Dict[str, Any]:
        seen_by_hook.append(copy.deepcopy(event))
        return event

    monkeypatch.setattr(config.sentry_sdk, "init", _init)
    # pytest's excepthook would turn the uncaught thread error into a warning.
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    try:
        config.init_sentry(
            "https://k@sentry.invalid/1",
            "test",
            traces_sample_rate=1.0,
            before_send=_hook,  # type: ignore[arg-type]
            **options,
        )
        trigger()
        sentry_sdk.flush()
    finally:
        real_init()  # detach the capture client
    return "".join(sent), seen_by_hook


def _frame_vars(node: Any) -> Iterator[Any]:
    """Every ``vars`` value in a parsed event (source lines may mention the word)."""
    if isinstance(node, dict):
        if "vars" in node:
            yield node["vars"]
        node = list(node.values())
    if isinstance(node, list):
        for item in node:
            yield from _frame_vars(item)


def _items(payload: str) -> List[Any]:
    return [json.loads(line) for line in payload.splitlines() if line]


@pytest.mark.parametrize("trigger", PATHS)
def test_local_variables_are_off_by_default(
    monkeypatch: pytest.MonkeyPatch, trigger: Callable[[], None]
) -> None:
    payload, seen_by_hook = _run(monkeypatch, trigger)

    assert '"frames"' in payload  # a stack trace was really sent
    assert seen_by_hook
    assert list(_frame_vars(_items(payload))) == []
    assert list(_frame_vars(seen_by_hook)) == []
    for text in (payload, repr(seen_by_hook)):
        for raw in (NAME, PHONE, EMAIL, *SECRETS):
            assert raw not in text


@pytest.mark.parametrize("trigger", PATHS)
def test_opting_in_sends_local_variables_still_scrubbed(
    monkeypatch: pytest.MonkeyPatch, trigger: Callable[[], None]
) -> None:
    payload, _ = _run(monkeypatch, trigger, include_local_variables=True)

    sent_vars = repr(list(_frame_vars(_items(payload))))
    assert NAME in sent_vars
    assert EMAIL not in payload
