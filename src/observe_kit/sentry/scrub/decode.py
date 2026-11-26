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
    return decoded, still_encoded(decoded)


def still_encoded(decoded: str) -> bool:
    """True when one more decode pass would still change ``decoded``.

    The shared exhaustion test: a value that is still changing once the cap is
    spent is treated by callers as unsafe to keep (redact, or defer to the redacting
    scrub) rather than restored.
    """
    return unquote(decoded) != decoded


def decode_scan(
    value: str, predicate: Callable[[str], bool], *, check_original: bool = True
) -> Tuple[str, bool, bool]:
    """Decode one level at a time until ``predicate`` holds, the value settles or the cap is hit.

    Returns ``(decoded, matched, capped)``. ``matched`` is True when ``predicate``
    held at some level (``decoded`` is that level). ``capped`` is True when every
    one of the :data:`MAX_DECODE_PASSES` passes changed the value without a match,
    so stability was never observed; callers decide the policy via
    :func:`still_encoded`. ``check_original=False`` skips testing ``value`` itself
    for callers that have already done so.
    """
    decoded = value
    if check_original and predicate(decoded):
        return decoded, True, False
    for _ in range(MAX_DECODE_PASSES):
        nxt = unquote(decoded)
        if nxt == decoded:
            return decoded, False, False
        decoded = nxt
        if predicate(decoded):
            return decoded, True, False
    return decoded, False, True


def decode_until(value: str, predicate: Callable[[str], bool]) -> Tuple[str, bool]:
    """Decode one level at a time until ``predicate`` holds or the cap is hit.

    Returns ``(decoded, matched)``: the value at the first level where
    ``predicate`` is true (``matched=True``) — including the *original* value,
    tested before any decoding, so a predicate that already holds returns the
    input unchanged — or the fully-decoded value if the predicate never held
    within the cap (``matched=False``). Shares the same :data:`MAX_DECODE_PASSES`
    budget as :func:`bounded_unquote`.
    """
    decoded, matched, _ = decode_scan(value, predicate)
    return decoded, matched


def unquote_n(value: str, levels: int) -> str:
    """Percent-decode exactly ``levels`` passes (stopping early once stable).

    For callers that must decode by a known encoding depth rather than until
    stable, so deeper-encoded octets stay encoded. ``levels`` is capped at
    :data:`MAX_DECODE_PASSES`, the same budget as the other seam helpers.
    """
    for _ in range(min(levels, MAX_DECODE_PASSES)):
        nxt = unquote(value)
        if nxt == value:
            break
        value = nxt
    return value
