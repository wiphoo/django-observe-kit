"""Load-tolerant check that work grows linearly with its input size.

Fixed wall-clock budgets were flaky: coverage tracing slows the scrubber about
4x and a loaded machine adds scheduling delays, so correct linear code missed
them. This compares the code against itself instead:

* ``thread_time`` ignores time spent waiting for a CPU;
* the garbage collector is off, as its pauses grow with the whole test
  session's heap rather than with the input;
* each size keeps its fastest of several runs.
"""

import gc
import time
from typing import Callable, TypeVar

T = TypeVar("T")

# Runs this cheap are noise-dominated and pass outright, so pick an ``n`` where
# a quadratic path costs well over this.
_FLOOR_SECONDS = 0.2
_STEP = 4
# Geometric midpoint of linear (STEP) and quadratic (STEP ** 2) growth, which
# leaves a factor of two of headroom on each side.
_MAX_RATIO = _STEP**1.5
_ATTEMPTS = 3


def _cpu_seconds(work: Callable[[T], object], value: T) -> float:
    enabled = gc.isenabled()
    gc.disable()
    try:
        started = time.thread_time()
        work(value)
        return time.thread_time() - started
    finally:
        if enabled:
            gc.enable()


def assert_scales_linearly(build: Callable[[int], T], work: Callable[[T], object], n: int) -> None:
    """Fail if ``work(build(n))`` grows much faster than linearly from ``n // 4``."""
    small = large = float("inf")
    for _ in range(_ATTEMPTS):
        large = min(large, _cpu_seconds(work, build(n)))
        if large <= _FLOOR_SECONDS:
            return
        small = min(small, _cpu_seconds(work, build(n // _STEP)))
        if large <= _MAX_RATIO * small:
            return
    raise AssertionError(
        f"superlinear: {large:.3f}s CPU at n={n}, {small:.3f}s at n={n // _STEP} "
        f"(ratio {large / max(small, 1e-9):.1f}, limit {_MAX_RATIO:.1f})"
    )
