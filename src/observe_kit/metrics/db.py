from __future__ import annotations

import time
from typing import Any, Callable

from django.db import connections


class QueryRecorder:
    """Wraps DB connections to count queries and total time."""

    def __init__(self) -> None:
        self.count = 0
        self.total_time = 0.0

    def __call__(
        self, execute: Callable[..., Any], sql: str, params: Any, many: bool, context: Any
    ) -> Any:
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.count += 1
            self.total_time += time.perf_counter() - start


class ConnectionWrappers:
    """Manages DB connection execute-wrappers for a single request.

    Registers *recorder* on every active Django DB connection when constructed,
    and removes all wrappers when :meth:`remove` (or ``__call__``) is invoked.
    Errors during removal are collected and re-raised after all wrappers are
    attempted, so a failure on one connection does not orphan the others.
    """

    def __init__(self, recorder: Callable[..., Any]) -> None:
        self._cms: list[Any] = []
        for connection in connections.all():
            cm = connection.execute_wrapper(recorder)
            cm.__enter__()
            self._cms.append(cm)

    def remove(self) -> None:
        """Remove all connection wrappers.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        errors: list[Exception] = []
        for cm in self._cms:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:
                errors.append(exc)
        self._cms.clear()
        if errors:
            raise errors[0]

    def __call__(self) -> None:
        self.remove()


def wrap_connections(recorder: Callable[..., Any]) -> ConnectionWrappers:
    """Register *recorder* on all active DB connections and return a handle to undo it."""
    return ConnectionWrappers(recorder)
