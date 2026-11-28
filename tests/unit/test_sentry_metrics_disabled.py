"""``init_sentry`` keeps Sentry metrics off: their attributes bypass PII scrubbing.

Metrics are not events, so neither ``before_send`` hook sees them. They go
through a real ``sentry_sdk.Client``; the assertions read what reaches the
transport.
"""

from typing import Any, Dict, List
from unittest.mock import patch

import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from observe_kit.sentry.config import init_sentry


class _CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.items: List[str] = []
        self.payloads: List[bytes] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.items += [item.type or "" for item in envelope.items]
        self.payloads.append(envelope.serialize())


def _init_options() -> Dict[str, Any]:
    with patch("observe_kit.sentry.config.sentry_sdk.init") as mock_init:
        init_sentry(dsn="https://k@sentry.invalid/1", environment="test")
    options = dict(mock_init.call_args[1])
    del options["integrations"]  # would patch Django globally; irrelevant here
    return options


def _emit(options: Dict[str, Any]) -> _CapturingTransport:
    transport = _CapturingTransport()
    client = sentry_sdk.Client(**options, transport=transport, default_integrations=False)
    attributes = {"email": "alice@example.com", "url": "https://h.test/?token=s3cret"}
    with sentry_sdk.new_scope() as scope:
        scope.set_client(client)
        sentry_sdk.metrics.count("signups", 1, attributes=attributes)
        sentry_sdk.metrics.gauge("queue", 1, attributes=attributes)
        sentry_sdk.metrics.distribution("latency", 1, attributes=attributes)
        sentry_sdk.logger.info("signup by alice@example.com")
        client.close()  # flushes the metric and log batchers
    return transport


def test_metrics_never_reach_the_transport() -> None:
    transport = _emit(_init_options())

    assert "trace_metric" not in transport.items
    sent = b"".join(transport.payloads)
    assert b"alice@example.com" not in sent
    assert b"s3cret" not in sent


def test_sentry_logs_stay_off_by_default() -> None:
    assert "log" not in _emit(_init_options()).items


def test_harness_sees_metrics_the_sdk_would_send() -> None:
    options = _init_options()
    options.pop("before_send_metric", None)

    transport = _emit(options)

    assert "trace_metric" in transport.items
    assert b"alice@example.com" in b"".join(transport.payloads)
