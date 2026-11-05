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
from typing import Callable, Iterator, List, Literal, NamedTuple, Pattern, Tuple

# Adjacent URLs joined by prose separators (``https://safe.test,postgres://u:p@h``).
_SEPARATOR_RE = re.compile(r"([,;|)\]}])(?=(?:[A-Za-z][A-Za-z0-9+.-]*:)?//)")

# Priority order: an earlier kind wins any overlap with a later one.
UrlKind = Literal["absolute", "relative", "rootless"]

Scanner = Callable[[str], Iterator[Tuple[int, int]]]


def _regex_scanner(pattern: Pattern[str]) -> Scanner:
    def scan(text: str) -> Iterator[Tuple[int, int]]:
        for match in pattern.finditer(text):
            yield match.span()

    return scan


# Body of an absolute or scheme-relative URL token (``//h/p``, ``//u:p@h/p``; the
# optional ``scheme:`` prefix is derived by ``_scheme_start``). Stops at prose
# separators and quote/angle delimiters; continues past ``,``/``;``/``)`` only when
# followed by ``&``-joined query structure (``?x=a,b&phone=...``).
_ABSOLUTE_BODY_RE = re.compile(
    r"//[^\s,;|)\]}<>\"']+"
    r"(?:[,;)][^\s&,;|)\]}<>\"']*&[^\s,;|)\]}<>\"']*=[^\s,;|)\]}<>\"']*)*"
)
_SCHEME_CHAR_RE = re.compile(r"[A-Za-z0-9+.-]", re.IGNORECASE)
_SCHEME_FIRST_RE = re.compile(r"[A-Za-z]", re.IGNORECASE)


def _scheme_start(
    text: str, slashes: int, floor: int, char_re: Pattern[str], first_re: Pattern[str]
) -> int:
    """Start of the leftmost ``scheme:`` ending right before the ``//`` at ``slashes``.

    Returns ``slashes`` itself when no scheme precedes it. ``floor`` is the earliest
    index a match may start at (the end of the previous match).
    """
    if slashes - 1 < floor or text[slashes - 1] != ":":
        return slashes
    run_start = slashes - 1
    while run_start - 1 >= floor and char_re.match(text, run_start - 1):
        run_start -= 1
    for index in range(run_start, slashes - 1):
        if first_re.match(text, index):
            return index
    return slashes


def _absolute_spans(text: str) -> Iterator[Tuple[int, int]]:
    # Anchor on ``//`` instead of retrying the optional ``scheme:`` prefix from every
    # letter of a long run, which was quadratic.
    pos = 0
    while True:
        slashes = text.find("//", pos)
        if slashes < 0:
            return
        body = _ABSOLUTE_BODY_RE.match(text, slashes)
        if body is None:
            pos = slashes + 1
            continue
        start = _scheme_start(text, slashes, pos, _SCHEME_CHAR_RE, _SCHEME_FIRST_RE)
        yield start, body.end()
        pos = body.end()


_ROOTLESS_RUN_RE = re.compile(r"[\w@.+/~-]+")
_ROOTLESS_FIRST_RE = re.compile(r"[\w@.+~-]")
_ROOTLESS_BAD_PREV_RE = re.compile(r"[\w/]")
_ROOTLESS_TAIL_RE = re.compile(r"[?#][\w.%\-]+=[^\s,;|)\]}<>\"']*")


def _rootless_spans(text: str) -> Iterator[Tuple[int, int]]:
    """Rootless relative URL with a key=value query or fragment.

    Examples: ``callback?phone=...``, ``account/callback?phone=...``. One tail match
    per maximal path run; the start is the leftmost valid index in that run.
    """
    pos = 0
    for run in _ROOTLESS_RUN_RE.finditer(text):
        run_start, run_end = run.span()
        if run_end <= pos or run_end >= len(text) or text[run_end] not in "?#":
            continue
        tail = _ROOTLESS_TAIL_RE.match(text, run_end)
        if tail is None:
            continue
        start = max(run_start, pos)
        while start < run_end and not (
            _ROOTLESS_FIRST_RE.match(text, start)
            and (start == 0 or not _ROOTLESS_BAD_PREV_RE.match(text, start - 1))
        ):
            start += 1
        if start == run_end:
            continue
        yield start, tail.end()
        pos = tail.end()


_RELATIVE_RUN_RE = re.compile(r"[^\s?#\"'<>]+")
_RELATIVE_TAIL_RE = re.compile(
    r"[?#]"
    r"[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*"
    r"(?:[,;][^\s&,;|)\]}<>\"']+&[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*)*"
)
_RELATIVE_STOP_RE = re.compile(r"[\s#,;|)\]}<>\"']")
_RELATIVE_BAD_PREV_RE = re.compile(r"[\w:/]")


def _relative_spans(text: str) -> Iterator[Tuple[int, int]]:
    """Relative URL starting with ``/`` that carries a ``key=value`` query or
    query-like fragment (``GET /search?phone=…``, ``/callback#access_token=…``).

    One tail match per maximal path run; a failed tail is remembered in ``fail_until``.
    """
    pos = 0
    fail_until = 0
    for run in _RELATIVE_RUN_RE.finditer(text):
        run_start, run_end = run.span()
        if (
            run_end <= pos
            or run_end >= len(text)
            or text[run_end] not in "?#"
            or run_end < fail_until
        ):
            continue
        tail = _RELATIVE_TAIL_RE.match(text, run_end)
        if tail is None:
            # No ``=`` before the next stop character: every ``?`` before it fails too.
            stop = _RELATIVE_STOP_RE.search(text, run_end + 1)
            fail_until = stop.start() if stop else len(text)
            continue
        start = text.find("/", max(run_start, pos), run_end)
        while start > 0 and _RELATIVE_BAD_PREV_RE.match(text, start - 1):
            start = text.find("/", start + 1, run_end)
        if start == -1:
            continue
        yield start, tail.end()
        pos = tail.end()


_KIND_SCANNERS: Tuple[Tuple[UrlKind, Scanner], ...] = (
    ("absolute", _absolute_spans),
    ("relative", _relative_spans),
    ("rootless", _rootless_spans),
)
ALL_KINDS: Tuple[UrlKind, ...] = tuple(kind for kind, _ in _KIND_SCANNERS)


class UrlSpan(NamedTuple):
    start: int
    end: int
    kind: UrlKind


def find_url_spans(text: str, kinds: Tuple[UrlKind, ...] = ALL_KINDS) -> List[UrlSpan]:
    """Return candidate URL spans in ``text``: ordered, non-overlapping.

    ``kinds`` limits which kinds are considered; priority between the kinds that
    are considered is absolute > relative > rootless.

    Production code calls this with a single kind per call (the legacy passes must
    each see the text rewritten by the previous pass), so the cross-kind priority
    resolution is only used when several kinds are requested together.
    """
    accepted: List[UrlSpan] = []
    starts: List[int] = []
    for kind, scan in _KIND_SCANNERS:
        if kind not in kinds:
            continue
        # One pattern's finditer matches are already disjoint, so only the spans
        # accepted from higher-priority kinds can conflict with a candidate.
        survivors: List[UrlSpan] = []
        for start, end in scan(text):
            i = bisect_left(starts, end)
            if i and accepted[i - 1].end > start:
                continue
            survivors.append(UrlSpan(start, end, kind))
        if survivors:
            accepted.extend(survivors)
            accepted.sort()
            starts = [span.start for span in accepted]
    return accepted


_NEXT_SCHEME_CHAR_RE = re.compile(r"[A-Za-z0-9+.-]")
_NEXT_SCHEME_FIRST_RE = re.compile(r"[A-Za-z]")
_USERINFO_SLASHES_RE = re.compile(r"//(?=[^/?#\s]*@)")


def _next_url_starts(text: str) -> Iterator[int]:
    pos = 0
    while True:
        slashes = text.find("//", pos)
        if slashes < 0:
            return
        start = _scheme_start(text, slashes, pos, _NEXT_SCHEME_CHAR_RE, _NEXT_SCHEME_FIRST_RE)
        if start != slashes or _USERINFO_SLASHES_RE.match(text, slashes):
            yield start
            pos = slashes + 2
        else:
            pos = slashes + 1


def split_url_token(token: str) -> List[Tuple[str, bool]]:
    """Split one absolute-URL token into ``(piece, is_url)`` pieces.

    Each URL (split at every further ``scheme://`` or ``//user@`` start) comes
    back as ``(piece, True)``. Only empty parts come back as ``(piece, False)``;
    a lone separator such as ``","`` is ``(",", True)``.
    ``"".join(piece for piece, _ in result) == token``.
    """
    pieces: List[Tuple[str, bool]] = []
    for part in _SEPARATOR_RE.split(token):
        if not part:
            pieces.append((part, False))
            continue
        start = 0
        for next_start in _next_url_starts(part):
            if next_start == 0:
                continue
            pieces.append((part[start:next_start], True))
            start = next_start
        pieces.append((part[start:], True))
    return pieces
