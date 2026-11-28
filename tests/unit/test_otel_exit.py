"""The process must exit when init_tracing() runs before Django configures logging.

Run in subprocesses because the bug only shows at interpreter shutdown. Django's
``setup()`` calls ``logging.config.dictConfig``, which flushes every existing
handler. The OTEL SDK's ``LoggingHandler.flush()`` started a non-daemon
``force_flush`` thread there. The export's own log records (connection errors, or
urllib3 DEBUG lines even with a healthy collector) re-fed the queue, so the
thread never finished and atexit never ran.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# A healthy run takes a few seconds and a broken one never ends, so this bound
# only needs to be far above the slowest CI startup.
_HANG_TIMEOUT = 60

_APP = """
import logging, sys
import django
from django.conf import settings
from observe_kit import init_tracing
from opentelemetry import trace

endpoint, level = sys.argv[1:]
logging.basicConfig(level=level)
init_tracing(service_name="exit-test", endpoint=endpoint)
logging.getLogger("app").warning("exit-test-log-record")
with trace.get_tracer("app").start_as_current_span("exit-test-span"):
    pass
settings.configure()
django.setup()
"""


def _run_app(endpoint: str, level: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "DJANGO_"))}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
    # Shortens the SDK's own retry window when nothing is listening.
    env["OTEL_EXPORTER_OTLP_TIMEOUT"] = "2"
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return subprocess.run(
        [sys.executable, "-c", _APP, endpoint, level],
        env=env,
        capture_output=True,
        text=True,
        timeout=_HANG_TIMEOUT,
    )


@pytest.fixture
def collector() -> Iterator[tuple[str, dict[str, bytes]]]:
    received: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received[self.path] = received.get(self.path, b"") + body
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()


def test_process_exits_when_no_collector_is_listening() -> None:
    # Bound but not listening: connections are refused and nobody can take the port.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        result = _run_app(f"http://127.0.0.1:{sock.getsockname()[1]}", "INFO")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_process_exits_and_exports_to_a_running_collector(
    collector: tuple[str, dict[str, bytes]], level: str
) -> None:
    endpoint, received = collector

    result = _run_app(endpoint, level)

    assert result.returncode == 0, result.stderr
    assert b"exit-test-log-record" in received["/v1/logs"]
    assert b"exit-test-span" in received["/v1/traces"]
