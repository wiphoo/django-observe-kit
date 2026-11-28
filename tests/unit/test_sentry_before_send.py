"""A user ``before_send`` composes with PII scrubbing instead of replacing it.

Events go through a real ``sentry_sdk.Client`` so the SDK's own handling of the
hook (including a raising one) is exercised, not assumed.
"""

import hashlib
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry.config import init_sentry

Event = Dict[str, Any]


class _CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: List[Event] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


def _send(
    event: Event,
    before_send: Optional[Callable[..., Any]],
    hint: Optional[Dict[str, Any]] = None,
    times: int = 1,
) -> List[Event]:
    with patch("observe_kit.sentry.config.sentry_sdk.init") as mock_init:
        init_sentry(
            dsn="https://k@sentry.invalid/1",
            environment="test",
            pii_level=PiiLevel.SENSITIVE,
            before_send=before_send,
        )
    options = dict(mock_init.call_args[1])
    del options["integrations"]  # would patch Django globally; irrelevant to the hooks
    transport = _CapturingTransport()
    client = sentry_sdk.Client(**options, transport=transport, default_integrations=False)
    for _ in range(times):
        client.capture_event(dict(event), hint=dict(hint or {}))
    return transport.events


def _event() -> Event:
    return {
        "message": "signup by alice@example.com",
        "request": {"headers": {"Authorization": "Bearer s3cret", "User-Agent": "agent/1"}},
        "user": {"ip_address": "203.0.113.7"},
    }


def test_user_hook_does_not_replace_scrubbing() -> None:
    (sent,) = _send(_event(), lambda event, hint: event)

    assert "alice@example.com" not in str(sent)
    assert "s3cret" not in str(sent)
    assert "203.0.113.7" not in str(sent)


def test_data_added_by_user_hook_is_scrubbed() -> None:
    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        event["extra"] = {"contact": "bob@example.com", "pair": ("carol@example.com",)}
        event["request"]["headers"]["Cookie"] = "session=abc123"
        return event

    (sent,) = _send(_event(), hook)

    for raw in ("bob@example.com", "carol@example.com", "abc123"):
        assert raw not in str(sent)


def test_returning_a_new_event_is_scrubbed() -> None:
    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        return {"event_id": event["event_id"], "message": "dave@example.com"}

    (sent,) = _send(_event(), hook)

    assert "dave@example.com" not in str(sent)


def test_user_hook_gets_the_event_before_scrubbing_and_the_hint() -> None:
    seen: List[Any] = []

    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        seen.append((event["message"], hint["marker"]))
        return event

    _send(_event(), hook, hint={"marker": 1})

    assert seen == [("signup by alice@example.com", 1)]


@pytest.mark.parametrize("hook", [None, lambda event, hint: event])
def test_scrubbing_is_applied_exactly_once(hook: Optional[Callable[..., Any]]) -> None:
    (sent,) = _send(_event(), hook)

    # init_sentry reads an empty PII_HASH_SALT here; a double pass would hash the hash.
    assert sent["request"]["headers"]["User-Agent"] == hashlib.sha256(b"agent/1").hexdigest()


def test_objects_attached_by_user_hook_are_not_mutated() -> None:
    app_owned = {"env": {"REMOTE_ADDR": "203.0.113.7"}}

    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        event["request"] = app_owned
        return event

    first, second = _send(_event(), hook, times=2)

    assert app_owned == {"env": {"REMOTE_ADDR": "203.0.113.7"}}
    assert first["request"] == second["request"]


def test_user_hook_returning_none_drops_the_event() -> None:
    assert _send(_event(), lambda event, hint: None) == []


@pytest.mark.parametrize("result", [True, "alice@example.com", [["email", "a@example.com"]]])
def test_user_hook_returning_a_non_dict_drops_the_event(result: Any) -> None:
    assert _send(_event(), lambda event, hint: result) == []


def test_raising_user_hook_drops_the_event() -> None:
    def hook(event: Event, hint: Dict[str, Any]) -> Event:
        raise RuntimeError("boom")

    assert _send(_event(), hook) == []


@pytest.mark.parametrize("hook", [None, lambda event, hint: event])
def test_raising_scrubber_drops_the_event(hook: Optional[Callable[..., Any]]) -> None:
    with patch("observe_kit.sentry.config.scrub_event", side_effect=RuntimeError("boom")):
        assert _send(_event(), hook) == []
