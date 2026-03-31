from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from django.db import connections

if TYPE_CHECKING:
    pass


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


def wrap_connections(recorder: Callable[..., Any]) -> Callable[[], list[Any]]:
    removers = []
    for connection in connections.all():
        context_manager = connection.execute_wrapper(recorder)
        context_manager.__enter__()
        removers.append(context_manager)
    return lambda: [remover.__exit__(None, None, None) for remover in removers]
