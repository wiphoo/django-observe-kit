"""The single bounded percent-decode seam for the Sentry PII scrubber.

Percent-encoding is the primary way PII hides from the scrubber (``%40`` for
``@``, ``%2F`` for ``/``, and nested ``%25`` layers that re-encode those). Every
place that needs to "decode until the hidden structure/PII is exposed" must go
through this one seam, so the decode cap and the exhaustion→redact policy live in
exactly one place instead of the copy-pasted loops that previously drifted apart.
"""

from __future__ import annotations

from typing import Callable, Tuple
from urllib.parse import unquote

# A value can be percent-encoded several layers deep (``%2540`` → ``%40`` →
# ``@``). Decode at most this many passes per seam call; an attacker can
# otherwise nest hundreds of layers to burn CPU inside ``before_send``.
# Legitimate values are one or two layers at most.
MAX_DECODE_PASSES = 5


def bounded_unquote(value: str) -> Tuple[str, bool]:
    """Percent-decode up to :data:`MAX_DECODE_PASSES`.

    Returns ``(decoded, exhausted)`` where ``exhausted`` is True when the cap was
    reached while the value was *still changing* — callers treat that as a
    conservative redaction signal for security-sensitive fragments.
    """
    decoded = value
    for _ in range(MAX_DECODE_PASSES):
        nxt = unquote(decoded)
        if nxt == decoded:
            return decoded, False
        decoded = nxt
    return decoded, unquote(decoded) != decoded


def decode_until(value: str, predicate: Callable[[str], bool]) -> Tuple[str, bool]:
    """Decode one level at a time until ``predicate`` holds or the cap is hit.

    Returns ``(decoded, matched)``: the value decoded to the first level where
    ``predicate`` is true (``matched=True``), or the fully-decoded value if the
    predicate never held within the cap (``matched=False``). Shares the same
    :data:`MAX_DECODE_PASSES` budget as :func:`bounded_unquote`.
    """
    decoded = value
    for _ in range(MAX_DECODE_PASSES):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
        if predicate(decoded):
            return decoded, True
    return decoded, False
