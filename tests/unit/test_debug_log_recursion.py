"""DEBUG logging outside a request must not recurse.

``RequestContextFilter`` calls ``get_request_context()``, which logs at DEBUG when
no context is set. If it logs before storing the context, the filter re-enters it
until ``RecursionError`` reaches the caller. Run in subprocesses to isolate the
global logging, Django and OTEL state.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_EMIT = """
import asyncio, contextvars, threading

errors = []

def emit(where):
    try:
        logging.getLogger("app").warning("hello from %s", where)
    except BaseException as exc:
        errors.append((where, type(exc).__name__))

async def task():
    emit("task")

# Thread and task go first, while no context has been stored anywhere.
thread = threading.Thread(target=contextvars.Context().run, args=(emit, "thread"))
thread.start()
thread.join()
contextvars.Context().run(asyncio.run, task())
contextvars.Context().run(emit, "main")
assert not errors, errors
"""

_MANUAL_ROOT_DEBUG = """
import logging
from observe_kit import configure_logging
configure_logging(level="DEBUG")
"""

_MANUAL_OBSERVE_KIT_DEBUG = """
import logging
from observe_kit import configure_logging
configure_logging(level="INFO")
logging.getLogger("observe_kit").setLevel("DEBUG")
"""

_AUTO_INIT = """
import logging
import django
from django.conf import settings
settings.configure(INSTALLED_APPS=["observe_kit"], OBSERVE_KIT={"LOG_LEVEL": "DEBUG"})
django.setup()
"""

# SERVICE_NAME makes ready() itself log through the filter, during django.setup().
_AUTO_INIT_WITH_TRACING = """
import logging
import django
from django.conf import settings
settings.configure(
    INSTALLED_APPS=["observe_kit"],
    OBSERVE_KIT={
        "LOG_LEVEL": "DEBUG",
        "SERVICE_NAME": "debug-test",
        "OTEL_ENDPOINT": "http://127.0.0.1:9",
    },
)
django.setup()
"""


@pytest.mark.parametrize(
    "setup",
    [_MANUAL_ROOT_DEBUG, _MANUAL_OBSERVE_KIT_DEBUG, _AUTO_INIT, _AUTO_INIT_WITH_TRACING],
    ids=["root-debug", "observe-kit-debug", "auto-init", "auto-init-tracing"],
)
def test_debug_logging_outside_request_does_not_recurse(setup: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "DJANGO_"))}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
    env["OTEL_EXPORTER_OTLP_TIMEOUT"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)

    result = subprocess.run(
        [sys.executable, "-c", setup + _EMIT], env=env, capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr[-3000:]
    assert "RecursionError" not in result.stderr
    for where in ("thread", "task", "main"):
        assert f'"message": "hello from {where}"' in result.stderr
    # The diagnostic itself is still logged.
    assert "get_request_context called outside a request context" in result.stderr
