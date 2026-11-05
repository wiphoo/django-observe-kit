"""The single URL *locate* seam for the Sentry PII scrubber.

This module only answers "where in this free text might a URL be?". It never
decides whether a candidate is really a URL or how to scrub it: every span is
handed to the ``urlsplit``-based scrubber in ``config.py``, which parses,
validates and redacts. A newly discovered URL-in-prose shape is therefore a
tweak to the patterns here, not another regex spread across ``config.py``.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from typing import List, Literal, NamedTuple, Pattern, Tuple

# Absolute or scheme-relative URL token (``https://h/p``, ``//u:p@h/p``,
# ``postgres://u:p@h/db``). Stops at prose separators and quote/angle
# delimiters; continues past ``,``/``;``/``)`` only when followed by ``&``-joined
# query structure (``?x=a,b&phone=…``).
_ABSOLUTE_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^\s,;|)\]}<>\"']+"
    r"(?:[,;)][^\s&,;|)\]}<>\"']*&[^\s,;|)\]}<>\"']*=[^\s,;|)\]}<>\"']*)*",
    re.IGNORECASE,
)

# Relative URL starting with ``/`` that carries a ``key=value`` query or
# query-like fragment (``GET /search?phone=…``, ``/callback#access_token=…``).
_RELATIVE_RE = re.compile(
    r"(?<![\w:/])/[^\s?#\"'<>]*[?#]"
    r"[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*"
    r"(?:[,;][^\s&,;|)\]}<>\"']+&[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*)*"
)

# Rootless relative URL (no leading ``/``) with a ``key=value`` query or fragment
# (``callback?phone=…``, ``account/callback?phone=…``).
_ROOTLESS_RE = re.compile(r"(?<![\w/])[\w@.+~-][\w@.+/~-]*[?#][\w.%\-]+=[^\s,;|)\]}<>\"']*")

# Adjacent URLs joined by prose separators (``https://safe.test,postgres://u:p@h``).
_SEPARATOR_RE = re.compile(r"([,;|)\]}])(?=(?:[A-Za-z][A-Za-z0-9+.-]*:)?//)")
_NEXT_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|//(?=[^/?#\s]*@)")

# Priority order: an earlier kind wins any overlap with a later one.
UrlKind = Literal["absolute", "relative", "rootless"]

_KIND_PATTERNS: Tuple[Tuple[UrlKind, Pattern[str]], ...] = (
    ("absolute", _ABSOLUTE_RE),
    ("relative", _RELATIVE_RE),
    ("rootless", _ROOTLESS_RE),
)
ALL_KINDS: Tuple[UrlKind, ...] = tuple(kind for kind, _ in _KIND_PATTERNS)


class UrlSpan(NamedTuple):
    start: int
    end: int
    kind: UrlKind


def find_url_spans(text: str, kinds: Tuple[UrlKind, ...] = ALL_KINDS) -> List[UrlSpan]:
    """Return candidate URL spans in ``text``: ordered, non-overlapping.

    ``kinds`` limits which kinds are considered; priority between the kinds that
    are considered is absolute > relative > rootless.
    """
    accepted: List[UrlSpan] = []
    starts: List[int] = []
    for kind, pattern in _KIND_PATTERNS:
        if kind not in kinds:
            continue
        # One pattern's finditer matches are already disjoint, so only the spans
        # accepted from higher-priority kinds can conflict with a candidate.
        survivors: List[UrlSpan] = []
        for match in pattern.finditer(text):
            start, end = match.span()
            i = bisect_left(starts, end)
            if i and accepted[i - 1].end > start:
                continue
            survivors.append(UrlSpan(start, end, kind))
        if survivors:
            accepted.extend(survivors)
            accepted.sort()
            starts = [span.start for span in accepted]
    return accepted


def split_url_token(token: str) -> List[Tuple[str, bool]]:
    """Split one absolute-URL token into ``(piece, is_url)`` pieces.

    Each URL (split at every further ``scheme://`` or ``//user@`` start) comes
    back as ``(piece, True)``. Only empty parts come back as ``(piece, False)``;
    a lone separator such as ``","`` is ``(",", True)`` because
    ``_SEPARATOR_RE.fullmatch`` needs the ``//`` lookahead and never matches alone.
    ``"".join(piece for piece, _ in result) == token``.
    """
    pieces: List[Tuple[str, bool]] = []
    for part in _SEPARATOR_RE.split(token):
        # the fullmatch clause is kept for legacy equivalence
        if not part or _SEPARATOR_RE.fullmatch(part):
            pieces.append((part, False))
            continue
        start = 0
        for match in _NEXT_URL_RE.finditer(part):
            if match.start() == 0:
                continue
            pieces.append((part[start : match.start()], True))
            start = match.start()
        pieces.append((part[start:], True))
    return pieces
