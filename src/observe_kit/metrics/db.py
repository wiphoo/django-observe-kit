from __future__ import annotations

import time
from typing import Callable

from django.db import connections


class QueryRecorder:
    """Wraps DB connections to count queries and total time."""

    def __init__(self) -> None:
        self.count = 0
        self.total_time = 0.0

    def __call__(self, execute, sql, params, many, context):  # type: ignore[override]
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.count += 1
            self.total_time += time.perf_counter() - start


def wrap_connections(recorder: Callable) -> Callable[[], None]:
    removers = []
    for connection in connections.all():
        remover = connection.execute_wrapper(recorder)
        removers.append(remover)
    return lambda: [remover() for remover in removers]
