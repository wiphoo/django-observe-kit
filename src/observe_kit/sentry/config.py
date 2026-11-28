from __future__ import annotations

import copy
import json
import logging
from bisect import bisect_right
from collections.abc import Hashable
from dataclasses import dataclass, field, replace
from itertools import accumulate, groupby, islice
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeGuard,
    cast,
)
from urllib.parse import (
    SplitResult,
    parse_qsl,
    quote,
    quote_plus,
    unquote,
    unquote_plus,
    urlparse,
    urlsplit,
    urlunsplit,
)

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.django import DjangoIntegration

from ..conf import (
    BODY_SECRET_KEYS,
    PII_SINK_SENTRY,
    QUERY_REDACTED,
    QUERY_SECRET_KEYS,
    cgi_header_name,
)
from ..pii_rules import (
    _EMAIL_FULLMATCH_RE,
    PiiLevel,
    _hash_value,
    _mask_value,
    effective_sets,
    get_pii_config,
    sanitize_body,
    sanitize_mapping,
    sanitize_query_params,
)
from ..pii_rules import _is_pair_list as _pii_is_pair_list
from .scrub.constants import REDACTED
from .scrub.decode import bounded_unquote as _bounded_unquote
from .scrub.decode import decode_scan, still_encoded, unquote_n
from .scrub.emails import _dedupe_masked_key, _mask_email_key, _mask_emails, _mask_emails_in_leaves
from .scrub.locate import _USERINFO_SLASHES_RE as _NESTED_USERINFO_RE
from .scrub.locate import (
    ALL_KINDS,
    UrlSpan,
    find_encoded_delims,
    find_encoded_url_spans,
    find_url_spans,
    split_url_token,
)

logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when configuration is invalid."""

    pass


def _validate_dsn(dsn: str) -> None:
    """Validate Sentry DSN."""
    if not dsn or not isinstance(dsn, str):
        raise ConfigurationError("dsn must be a non-empty string")
    if not dsn.startswith(("http://", "https://")):
        raise ConfigurationError(
            "dsn must be a valid Sentry DSN URL starting with http:// or https://"
        )
    try:
        urlparse(dsn)
    except Exception as e:
        raise ConfigurationError(f"dsn must be a valid URL: {e}") from e


def _validate_environment(environment: str) -> None:
    """Validate environment name."""
    if not environment or not isinstance(environment, str):
        raise ConfigurationError("environment must be a non-empty string")
    if len(environment) > 64:
        raise ConfigurationError("environment must be 64 characters or less")


def _validate_traces_sample_rate(traces_sample_rate: float) -> None:
    """Validate traces sample rate."""
    if not isinstance(traces_sample_rate, (int, float)):
        raise ConfigurationError("traces_sample_rate must be a number")
    if not 0.0 <= traces_sample_rate <= 1.0:
        raise ConfigurationError("traces_sample_rate must be between 0.0 and 1.0")


# Cookies are redacted wholesale (see ``scrub_event``): session/auth cookies can
# appear under any key and in non-dict shapes, so key-based scrubbing can't
# guarantee no raw cookie leaks. Matches Sentry's own "[Filtered]" convention.
_REDACTED = REDACTED

# A URL whose query holds another URL (``next=/p?next=/p?…``) re-enters the URL
# scrubber once per level. An attacker can nest hundreds of levels to blow the
# Python stack inside ``before_send`` (dropping the event) and balloon the
# ``urlencode`` output, so a recursion budget is threaded through the URL-scrub
# cycle and the value is redacted once it is exhausted. Legitimate nested
# redirects are only a level or two deep.
_MAX_URL_NESTING = 20

# The embedded and percent-encoded email scanners (``find_email_spans`` /
# ``find_encoded_email_spans``) live in ``pii_rules`` alongside ``_BARE_EMAIL_RE`` so
# email grammar has a single owning module; ``_EMAIL_FULLMATCH_RE`` is imported above.

# Well-known keys whose *value* is a full or relative URL / a bare path. These
# are the OTel/Sentry semantic span-data attribute names (plus the plain ``url``
# key), lower-cased for matching. ``http.target`` is a path+query while
# ``url.path`` and the ``http.path`` tag are bare paths (this repo's OTel
# middleware / SentryContextMiddleware set them from ``request.get_full_path()``
# / ``request.path``); ``urlsplit`` parses the relative forms fine, so
# ``_scrub_url`` scrubs their query / path emails (including encoded ``%40``).
_URL_VALUE_KEYS = frozenset({"url", "http.url", "url.full", "http.target", "url.path", "http.path"})
_QUERY_VALUE_KEYS = frozenset({"http.query", "url.query"})
# A bare URL fragment (no leading ``#``), as the Sentry HTTP integrations emit it.
_FRAGMENT_VALUE_KEYS = frozenset({"http.fragment", "url.fragment"})

# Keys whose value is a DB/cache statement. Its literals can't be parsed out, so
# the value is redacted wholesale (matching the db/cache span-*description*
# treatment in :func:`_scrub_span_description`). ``db.query.text`` is the current
# OpenTelemetry semantic-convention name (``db.statement`` is the legacy one).
_STATEMENT_VALUE_KEYS = frozenset({"db.statement", "db.query", "db.query.text", "statement"})

# Key prefixes whose value is a DB statement fragment, redacted wholesale. Covers
# the OTel ``db.query.parameter.<name>`` bound-parameter attributes.
_STATEMENT_KEY_PREFIXES = ("db.query.parameter.",)

# Keys whose string value is free text that may embed PII (e.g. a Sentry log
# message or an exception's ``value``). Pattern-scrubbed via :func:`_scrub_text`.
_TEXT_VALUE_KEYS = frozenset({"message", "formatted", "value"})

# Keys whose entire subtree is free text — every string leaf is pattern-scrubbed.
# ``logentry.params`` holds the raw interpolation args of a ``%``-formatted log
# message (e.g. ``logger.error("user=%s", email)`` → ``params=[email]``).
_TEXT_SUBTREE_KEYS = frozenset({"params"})

# The final `_scrub_value_fields` walk is the single authority for the
# URL / query / free-text field rules (see `_content_opts`). The walk-managed
# key set is *derived* from the `_WALK_RULES` registry (defined next to the
# handlers, below), so the registry, the `_content_opts` stripping, and the walk
# can never drift apart when a new key is added.

# Request header names that carry the client IP behind a proxy. Hashed at
# SENSITIVE (like the client IP) since ``HASH_FIELDS`` only matches ``ip``.
_IP_HEADER_NAMES = frozenset(
    {
        "x-forwarded-for",
        "x-real-ip",
        "x-client-ip",
        "true-client-ip",
        "cf-connecting-ip",
        "forwarded",
    }
)

# Request header names whose value is a full URL (its query can carry PII). The
# key-based rules pass these through and the value walk doesn't match header
# names, so scrub them with ``_scrub_url`` in dict / pair-list / CGI-env forms.
_URL_HEADER_NAMES = frozenset(
    {"referer", "referrer", "content-location", "location", "x-original-url", "x-rewrite-url"}
)

# CGI/WSGI env keys whose value mirrors the request URI (path[+query]) — they
# carry the same path/query PII as ``request.url`` and are scrubbed with
# ``_scrub_url``. ``PATH_INFO`` is path-only but can hold an email path segment.
_URI_ENV_KEYS = frozenset({"REQUEST_URI", "RAW_URI", "PATH_INFO"})


@dataclass(frozen=True)
class _PiiOpts:
    """Bundles the per-sink PII settings threaded through the scrubbers."""

    level: PiiLevel
    hash_salt: str = ""
    extra_drop: Optional[FrozenSet[str]] = None
    extra_mask: Optional[FrozenSet[str]] = None
    extra_hash: Optional[FrozenSet[str]] = None
    # Resolved (built-in | operator-extra) drop/mask/hash sets, computed once per
    # event instead of re-unioning on every string leaf in the before_send walk.
    # Excluded from init/compare/repr; recomputed automatically by ``replace``.
    rule_sets: Tuple[Set[str], Set[str], Set[str]] = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "rule_sets", effective_sets(self.extra_drop, self.extra_mask, self.extra_hash)
        )

    def mapping(self, m: Mapping[str, str]) -> MutableMapping[str, str]:
        return sanitize_query_params(
            m,
            self.level,
            extra_drop=self.extra_drop,
            extra_mask=self.extra_mask,
            extra_hash=self.extra_hash,
            hash_salt=self.hash_salt,
        )

    def body(self, b: Any) -> Any:
        return sanitize_body(
            b,
            self.level,
            extra_drop=self.extra_drop,
            extra_mask=self.extra_mask,
            extra_hash=self.extra_hash,
            hash_salt=self.hash_salt,
        )


def _content_opts(opts: _PiiOpts) -> _PiiOpts:
    """Return ``opts`` with the walk-managed keys stripped from the extra sets.

    Used for the pre-walk body/mapping passes over user content (``extra``,
    ``contexts``, ``request.data`` dict, ``tags``, span/breadcrumb data, frame
    vars, ``user``). URL / query / free-text values are rewritten by the final
    :func:`_scrub_value_fields` walk, which is the single place that applies the
    operator field rule to a ``_WALK_MANAGED_KEYS`` key — so ``request.url`` (no
    earlier pass) and the same key nested in a body-scrubbed container are
    scrubbed identically. Applying the rule here too would hash the value a
    second time in the walk (and desync a hashed ``url`` from ``request.url``),
    so drop those keys from the operator sets. Non-walk keys (``email``,
    ``phone``, ``authorization`` …) are untouched, and pair-list passes keep the
    full ``opts`` because the walk treats a ``[key, value]`` pair as bare leaves.
    """

    def _minus(s: Optional[FrozenSet[str]]) -> Optional[FrozenSet[str]]:
        if not s:
            return s
        remaining = s - _WALK_MANAGED_KEYS
        return remaining or None

    return replace(
        opts,
        extra_drop=_minus(opts.extra_drop),
        extra_mask=_minus(opts.extra_mask),
        extra_hash=_minus(opts.extra_hash),
    )


def _check_field_rule(key: str, opts: _PiiOpts) -> Optional[str]:
    drop, mask, hsh = opts.rule_sets
    if key in drop:
        return "drop"
    if key in mask:
        return "mask"
    if opts.level == PiiLevel.SENSITIVE and key in hsh:
        return "hash"
    # A serialized byte key (``"b'phone'"``) coexisting with its plain form is
    # kept distinct by ``_normalize_bytes_leaves`` (no clobber); still match its
    # *unwrapped* name so the field rule applies to the repr-keyed value too.
    stripped = _strip_bytes_repr_key(key)
    if stripped != key:
        if stripped in drop:
            return "drop"
        if stripped in mask:
            return "mask"
        if opts.level == PiiLevel.SENSITIVE and stripped in hsh:
            return "hash"
    return None


def _field_has_mask_rule(key_lower: str, opts: _PiiOpts) -> bool:
    if opts.level not in {PiiLevel.BASIC, PiiLevel.SENSITIVE}:
        return False
    _, mask, _ = opts.rule_sets
    if key_lower in mask:
        return True
    stripped = _strip_bytes_repr_key(key_lower)
    return stripped != key_lower and stripped in mask


def _check_value_rule(key: str, value: str, opts: _PiiOpts) -> Tuple[Optional[str], Any]:
    rule = _check_field_rule(key, opts)
    if rule == "drop":
        return ("drop", None)
    if rule == "mask":
        return ("mask", _REDACTED)
    if rule == "hash":
        return ("hash", _hash_value(value, opts.hash_salt))
    return (None, None)


def _scrub_masked_field_result(key_lower: str, original: Any, scrubbed: Any, opts: _PiiOpts) -> Any:
    if isinstance(original, (dict, list, tuple, set, frozenset)) and _field_has_mask_rule(
        key_lower, opts
    ):
        return _REDACTED
    return _scrub_text(scrubbed, opts) if isinstance(scrubbed, str) else scrubbed


def _fragment_carries_pii(fragment: str) -> bool:
    decoded, exhausted = _bounded_unquote(fragment)
    return exhausted or "=" in decoded or "@" in decoded


def _is_sha256_hex(value: Any) -> bool:
    """True when ``value`` is a 64-char lowercase hex SHA256 digest.

    Used to tell an operator *hash* rule's safe digest apart from a *mask*
    rule's ``_mask_value`` output (which preserves everything after the first
    ``@`` / whitespace and must therefore be redacted wholesale, not kept).
    """
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _redact_partial_email_mask(original: Any, masked: Any) -> Any:
    """Redact an operator mask result wholesale when it is a *partial* email mask.

    ``_mask_value`` masks only the local part of an email and keeps everything
    after the domain, so a matched value like ``alice@example.com supersecret``
    becomes ``a***@example.com supersecret`` — leaking the trailing secret. When
    the original was more than a bare email (fails :data:`_EMAIL_FULLMATCH_RE` ``fullmatch``),
    redact the whole value. A SHA256 hash or a clean whole-value email mask
    (``a***@example.com``) is kept as-is.
    """
    if _is_sha256_hex(masked):
        return masked
    if (
        isinstance(original, str)
        and "@" in original
        and not _EMAIL_FULLMATCH_RE.fullmatch(original)
    ):
        return _REDACTED
    return masked


_BRACKET_SENTINELS = str.maketrans({"[": "\ue000", "]": "\ue001"})
_BRACKET_RESTORE = str.maketrans({"\ue000": "[", "\ue001": "]"})


def _split_repaired(value: str) -> Optional[tuple[SplitResult, bool]]:
    """``urlsplit`` ``value``, repairing it first if ``urlsplit`` rejects it.

    ``urlsplit`` raises ``ValueError`` on a stray/unbalanced IPv6 bracket
    (``http://[``, ``https://safe.test[%252F…``). The repair swaps every ``[``/``]``
    for a private-use sentinel so the *parser* succeeds, then maps the sentinels
    back in each component, so the returned parts carry the original text and the
    malformed URL is never reserialised into something else. Returns
    ``(parts, repaired)`` or ``None`` when the URL can't be parsed even then (the
    caller redacts it wholesale).
    """
    try:
        return urlsplit(value), False
    except ValueError:
        pass
    if "\ue000" in value or "\ue001" in value:
        return None
    try:
        parts = urlsplit(value.translate(_BRACKET_SENTINELS))
    except ValueError:
        return None
    return SplitResult(*(c.translate(_BRACKET_RESTORE) for c in parts)), True


def _split_path(path: str) -> tuple[str, str, str]:
    """Split ``path`` (possibly carrying a literal ``?``/``#``) into path/query/fragment.

    Delegates to ``urlsplit`` behind a dummy authority so a path that merely starts
    with ``//`` (``//[redirect?…``) is not read as a new authority.
    """
    rooted = path[:1] in ("/", "?", "#", "")
    parts = urlsplit("s://h" + ("" if rooted else "/") + path.translate(_BRACKET_SENTINELS))
    head = parts.path if rooted else parts.path[1:]
    return (
        head.translate(_BRACKET_RESTORE),
        parts.query.translate(_BRACKET_RESTORE),
        parts.fragment.translate(_BRACKET_RESTORE),
    )


def _hides_query_or_frag_delim(text: str) -> bool:
    return next(find_encoded_delims(text, "?#"), None) is not None


def _expose_query_frag_delims(text: str) -> str:
    """Replace every encoded query/fragment delimiter (any depth) with its literal."""
    out: List[str] = []
    pos = 0
    for start, end, delim in find_encoded_delims(text, "?#"):
        out.extend((text[pos:start], delim))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _repair_url(value: str) -> Optional[str]:
    """Normalise a URL value so :func:`_scrub_url` can drive off ``urlsplit`` alone.

    Percent-encoded delimiters hide structure from ``urlsplit`` (``%3F``/``%23`` in a
    path or authority, ``%40`` userinfo), so the value is bounded-decoded via the
    :mod:`scrub.decode` seam and the *encoded* delimiters are exposed — only the
    delimiters, so unrelated escapes (``%2F``/``%20``) stay intact. Every parse goes
    through :func:`_split_repaired`, so an unparseable bracket URL is repaired rather
    than hand-parsed. Returns ``value`` when nothing is hidden, and ``None`` when
    the decode cap is exhausted or either form is unparseable (the caller redacts
    the value wholesale).
    """
    decoded, exhausted = _bounded_unquote(value)
    if exhausted:
        return None
    if decoded == value:
        return value
    orig = _split_repaired(value)
    dec = _split_repaired(decoded)
    if orig is None or dec is None:
        return None  # decoded form hides structure we can't parse: fail closed
    orig_parts, dec_parts = orig[0], dec[0]
    # An encoded query/fragment delimiter hidden in the *path* is structure even
    # when the URL already carries a visible outer query.
    authority_revealed = "@" in dec_parts.netloc and "@" not in orig_parts.netloc
    # A delimiter can hide in the *authority* too, when the path-separating ``/`` is
    # itself encoded (``https://host!%252Fsearch%253Fphone%253D…``): ``urlsplit``
    # then sees the whole thing as the netloc.
    netloc_hides_structure = _hides_query_or_frag_delim(orig_parts.netloc)
    path_hides_structure = _hides_query_or_frag_delim(orig_parts.path)
    if not (path_hides_structure or authority_revealed or netloc_hides_structure):
        return value  # only ordinary escapes decoded — keep the original form
    if netloc_hides_structure and not authority_revealed:
        return decoded  # buried in the authority: reparse the decoded form
    if authority_revealed and not path_hides_structure:
        # Fully encoded structure (no visible scheme) -> reparse the decoded form;
        # otherwise only the authority was encoded: keep the original path escapes.
        if orig_parts.scheme:
            return urlunsplit(orig_parts._replace(netloc=dec_parts.netloc))
        return decoded
    # Expose the encoded delimiters *within the path only*; a pre-existing outer
    # query/fragment is preserved rather than merged into the exposed one.
    exposed_path = _expose_query_frag_delims(orig_parts.path)
    path_only, hidden_query, path_fragment = _split_path(exposed_path)
    # Decode the hidden query by exactly the outer query delimiter's encoding depth
    # (``%3F`` -> one level, ``%253F`` -> two, ...) so ``parse_qsl`` splits the pairs
    # while a more deeply encoded octet inside a value stays encoded.
    if hidden_query:
        qspan = next(find_encoded_delims(orig_parts.path, "?"), None)
        levels = (qspan[1] - qspan[0] - 3) // 2 + 1 if qspan else 1
        hidden_query = unquote_n(hidden_query, levels)
    query = (
        f"{hidden_query}&{orig_parts.query}"
        if hidden_query and orig_parts.query
        else (hidden_query or orig_parts.query)
    )
    return urlunsplit(
        orig_parts._replace(
            path=path_only, query=query, fragment=path_fragment or orig_parts.fragment
        )
    )


def _value_carries_url(value: str) -> bool:
    """True when a query-parameter value is itself a URL or carries a nested query.

    A ``next`` / ``redirect`` parameter often holds an absolute URL, a
    scheme-relative ``//user:secret@host/path`` (whose ``user:pass@`` credentials
    ``_scrub_url`` redacts), or a relative path with its own ``?key=value`` query
    (``next=/search?phone=…``) or a query-like ``#key=value`` fragment
    (``next=/callback#access_token=…`` — an OAuth implicit-flow token).
    ``parse_qsl`` decodes it into the value, so it needs a full URL scrub, not
    just email masking, or the inner parameters / credentials / fragment tokens
    are re-emitted raw. The scheme test is case-insensitive (``HTTPS://…`` is
    still a URL).
    """
    return (
        value[:8].lower().startswith(("http://", "https://"))
        or value.startswith("//")
        # embedded scheme-relative URL in free text
        or bool(find_url_spans(value, kinds=("absolute",)))
        or "://" in value  # any URI scheme: postgres://, redis://, ftp://, …
        # A relative (``/search?phone=…``) or rootless (``callback?phone=…``)
        # URL token carrying a query/fragment. These require the ``?key=value`` to
        # be *contiguous* with a path token, so conversational prose with a stray
        # ``?``/``#`` and a later ``=`` (``Are you sure? answer=yes``,
        # ``prefix# section=one``) is not misclassified as a URL and mangled by
        # the structural parser — such a leaf is left to the email backstop.
        or bool(find_url_spans(value, kinds=("relative",)))
        or bool(find_url_spans(value, kinds=("rootless",)))
        # A *bare* query/fragment token that leads the value (``#access_token=…``
        # exposed from an encoded ``%23``, ``?key=value``) — no path prefix for the
        # URL locator to anchor on. Require no whitespace so it stays a URL-ish
        # token and prose (which carries its ``?``/``#`` mid-sentence, or contains
        # spaces) is still excluded.
        or (value[:1] in "?#" and "=" in value and not any(c.isspace() for c in value))
    )


# A non-URL prefix glued to a nested relative URL (``redirect:/search?…``,
# ``x=/p?…``, ``/docs'redirect:/p?…``) — the URL locators reject a relative URL after
# such a prefix, so :func:`_value_carries_url` alone would leave its query
# unscrubbed. The delimiter must be directly followed by ``/`` or ``?``.
_URL_PREFIX_CHARS = frozenset(":;=,'\"")
_MAX_PREFIX_SPLIT_TRIES = 8  # bound the rescans so a ``:/:/:/…`` value stays linear


def _prefixed_url_split(value: str) -> Optional[Tuple[str, str]]:
    """Split ``prefix`` + nested URL when ``value`` is a prefixed relative URL, else ``None``."""
    prefix_ends = (
        i + 1
        for i in range(len(value) - 1)
        if value[i] in _URL_PREFIX_CHARS and value[i + 1] in "/?"
    )
    for end in islice(prefix_ends, _MAX_PREFIX_SPLIT_TRIES):
        if _value_carries_url(value[end:]):
            return value[:end], value[end:]
    return None


def _scrub_carried_url(value: str, opts: _PiiOpts, depth: int = 0) -> Optional[str]:
    """Scrub ``value`` if it is a URL or a prefixed nested URL, else ``None``."""
    if _value_carries_url(value):
        return _scrub_urlish_value(value, opts, depth)
    split = _prefixed_url_split(value)
    if split:
        return split[0] + _scrub_urlish_value(split[1], opts, depth)
    return None


def _scrub_url_token(token: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Scrub one absolute-URL token, scrubbing each adjacent URL independently."""
    return "".join(
        _scrub_url(piece, opts, depth) if is_url else piece
        for piece, is_url in split_url_token(token)
    )


def _scrub_urlish_value(value: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Scrub a URL-like value, treating mixed prose as free text."""
    # Locate absolute and relative URLs *independently* (not in one combined
    # call): a relative match that starts earlier must win even when it overlaps
    # an absolute one, which a combined call would drop by priority.
    spans = _absolute_and_relative_spans(value)
    if not spans:
        rootless = find_url_spans(value, kinds=("rootless",))
        if rootless and not (rootless[0].start == 0 and rootless[0].end == len(value)):
            return _scrub_text(value, opts, depth)  # prose around a rootless URL
        return _scrub_url(value, opts, depth)
    first = min(spans, key=lambda s: s.start)
    if first.start == 0 and first.end == len(value):
        if "%" in value:
            # Any interior match (something precedes it) means the token is
            # mixed content — route it through the free-text scrubber. An
            # end-aligned match counts too: a visible URL followed by an
            # encoded one leaks if the encoded suffix is treated as a plain
            # path by the visible-URL parser.
            _, mixed, _ = decode_scan(
                value,
                lambda d: any(s.start > 0 for s in _absolute_and_relative_spans(d)),
                check_original=False,
            )
            if mixed:
                return _scrub_text(value, opts, depth)
        return _scrub_url_token(value, opts, depth)
    return _scrub_text(value, opts, depth)


def _absolute_and_relative_spans(value: str) -> List[UrlSpan]:
    """Absolute spans, then relative spans, each kind located on its own."""
    return find_url_spans(value, kinds=("absolute",)) + find_url_spans(value, kinds=("relative",))


def _hides_url_structure(raw: str) -> bool:
    """True when a query key/value, decoded as deep as the scrubber does, holds ``? # //``."""
    decoded, _ = _bounded_unquote(unquote_plus(raw))
    return "?" in decoded or "#" in decoded or "//" in decoded


def _outer_scrub_handles(raw: str) -> bool:
    """True when :func:`_scrub_url_if_carries` would scrub this raw query key/value.

    Mirrors its decode loop (``parse_qsl`` decodes once, then up to the fixed pass
    limit, and a value still encoded after that is redacted), so the hidden-URL
    pass can leave exactly those slices to the outer scrub.
    """
    decoded, matched, capped = decode_scan(unquote_plus(raw), _value_carries_url)
    if matched:
        return True
    if capped:
        return still_encoded(decoded)  # exhausted: the outer scrub redacts it
    return _prefixed_url_split(decoded) is not None


def _leads_with_query(value: str) -> bool:
    """A value that starts with ``?`` / ``#`` and holds a ``=`` (``?phone=0812345678 ok``).

    :func:`_value_carries_url` skips these when they contain whitespace (prose); the
    free-text scrubber finds the query run inside and leaves the prose untouched.
    """
    return value[:1] in "?#" and "=" in value


def _scrub_leading_query(value: str, opts: _PiiOpts, depth: int) -> str:
    """Free-text scrub of a :func:`_leads_with_query` value.

    The URL locators need a path to anchor the query on, so a throwaway ``/`` is
    prefixed for the scrub and stripped again.
    """
    out = _scrub_text("/" + value, opts, depth)
    if not out.startswith("/"):
        return _REDACTED  # lost its anchor: fail closed
    # The caller re-quotes the value, so drop the scrubber's own escapes (``%2A``
    # masks) unless the value carried literal ``%`` data of its own.
    return out[1:] if "%" in value else unquote(out[1:])


def _scrub_url_if_carries(value: str, opts: _PiiOpts, depth: int = 0) -> Optional[str]:
    """Return the scrubbed value if it is / carries a URL, else ``None``.

    ``parse_qsl`` decodes a query value only once, so a multiply encoded redirect
    can still arrive here without literal URL structure. Decode with a small
    fixed limit until the value stabilizes or exposes a URL, then scrub the
    decoded form. ``None`` lets the caller fall back to :func:`_mask_emails`.

    A value that carries a URL in free text (``"connect https://user:secret@host/db"``)
    is sent through :func:`_scrub_text`, which uses the URL locator to find individual
    URL tokens so ``urlsplit`` receives a clean URL and can strip ``user:pass@``
    credentials from the netloc.
    """
    if _value_carries_url(value):
        return _scrub_urlish_value(value, opts, depth)
    if _leads_with_query(value):
        return _scrub_leading_query(value, opts, depth)
    decoded, matched, capped = decode_scan(
        value, lambda v: _value_carries_url(v) or _leads_with_query(v), check_original=False
    )
    if matched:
        if _value_carries_url(decoded):
            return _scrub_urlish_value(decoded, opts, depth)
        return _scrub_leading_query(decoded, opts, depth)
    if capped and still_encoded(decoded):
        return _REDACTED
    # Only once fully decoded: a prefixed nested URL (``redirect:/search?…``) —
    # trying it earlier would scrub just the tail of a still-encoded value.
    split = _prefixed_url_split(decoded)
    if split:
        return split[0] + _scrub_urlish_value(split[1], opts, depth)
    return None


def _scrub_custom_url_header(name_lower: str, value: str, opts: _PiiOpts) -> str:
    """Scrub a custom (non-allowlisted) URL-carrying header value.

    An explicit ``EXTRA_MASK_FIELDS`` rule may have already partially masked the
    value via ``header_opts.mapping`` (``https://a:pw@host/x`` → ``h***@host/x``),
    which ``_value_carries_url`` no longer recognises as a URL — so the path
    secret would survive. When a mask rule matched and that partial mask left a
    corrupted URL/credential (an ``@`` with trailing content), redact the whole
    value; a benign partial mask (an IP ``20***``) is kept. Otherwise apply the
    normal URL backstop and leave non-URL values untouched.
    """
    if _check_field_rule(name_lower, opts) == "mask":
        return cast(str, _redact_partial_email_mask(value, value))
    scrubbed = _scrub_url_if_carries(value, opts)
    return scrubbed if scrubbed is not None else value


def _set_pair_value(container: Any, index: int, value: Any) -> None:
    """Write ``value`` into the second slot of the ``[name, value]`` pair at ``container[index]``.

    Header pairs arrive as either ``[name, value]`` lists or ``(name, value)``
    tuples. A tuple is immutable, so an in-place ``pair[1] = value`` raises
    ``TypeError`` (aborting before_send and dropping the whole event), while
    copying the tuple to a fresh local and mutating that silently discards the
    scrub. Mutate a list pair in place (works whatever the outer container is);
    for a tuple pair, replace the whole element, which requires a list container.
    A tuple pair inside a tuple container is immutable end-to-end and left as-is.
    """
    item = container[index]
    if isinstance(item, list):
        item[1] = value
    elif isinstance(container, list):
        container[index] = (item[0], value)


def _scrub_url_values(obj: Any, opts: _PiiOpts) -> None:
    """Apply ``_scrub_url_if_carries`` to string values in ``obj`` (dict/pair list), in place.

    Custom *request headers* that carry URL-looking values (e.g. ``X-Next:
    /search?phone=…``) are not covered by the allowlist ``_scrub_url_headers``
    (which only handles well-known URL headers like ``Referer``), and they are
    saved out of the whole-event walk before the value walk runs. This gives
    them the URL/query scrub before the save, so query PII in custom headers is
    not left for the email-only restore path.

    Known URL headers are skipped — they were already fully scrubbed by the
    request block, so a second URL scrub would hash an already-hashed nested
    param a second time (``token=sha256(sha256(secret))``), desyncing it from
    the single-hash ``request.url``. The CGI ``request.env`` needs no such pass:
    :func:`_scrub_env` scrubs its custom URL-carrying headers in the single env
    pass.
    """
    if isinstance(obj, dict):
        for key in list(obj):
            value = obj[key]
            if isinstance(value, str) and value and isinstance(key, str):
                # CGI env keys (HTTP_REFERER → referer) must be normalised before
                # the header-name check, otherwise an already-scrubbed URL header
                # like ``HTTP_REFERER`` would be re-scrubbed (double-hash).
                check_key = cgi_header_name(key) or str(key).lower()
                if check_key not in _URL_HEADER_NAMES:
                    obj[key] = _scrub_custom_url_header(check_key, value, opts)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], str):
                name = str(item[0]).lower()
                if name not in _URL_HEADER_NAMES:
                    _set_pair_value(obj, i, _scrub_custom_url_header(name, item[1], opts))


def _rule_key(key: str) -> Optional[str]:
    """A query key as the field rules must see it; ``None`` when the decode cap is exhausted.

    A key encoded deeper than its pair (``ph%256fne=…``) survives the one decode
    ``parse_qsl`` performs and would miss its field rule, while a consumer that
    decodes once more still reads ``phone``.
    """
    if "%" not in key:
        return key
    decoded, exhausted = _bounded_unquote(key)
    return None if exhausted else decoded


def _apply_key_rules(key: Any, rule_key: Any, value: Any, opts: _PiiOpts) -> Tuple[bool, Any]:
    """Run the field rules on a *query* key as written *and* its decoded alias.

    ``(False, None)`` when either spelling is dropped, else ``(True, value')`` where
    ``value'`` is the changed value if any rule masked/hashed it. An operator rule
    naming the literal key (``a%20b``) must keep applying after the decode. A
    non-empty value of a :data:`QUERY_SECRET_KEYS` key that no operator rule changed
    is redacted. Every query-parameter shape routes through here.
    """
    probe = {key: value, rule_key: value}
    out = opts.mapping(probe)
    if any(k not in out for k in probe):
        return False, None
    for k in probe:
        if out[k] != value:
            return True, out[k]
    if value not in ("", None) and _is_secret_key(probe, QUERY_SECRET_KEYS):
        return True, QUERY_REDACTED
    return True, value


def _is_secret_key(keys: Any, secrets: FrozenSet[str]) -> bool:
    return any(_strip_bytes_repr_key(str(k).lower()) in secrets for k in keys)


def _redact_body_secrets(node: Any, opts: _PiiOpts) -> Any:
    """Redact unambiguous credential fields of a parsed request body (returns a copy).

    Runs before the body rules. The whole subtree under a key that an active
    drop/mask/hash rule acts on is left raw, so that rule is applied once, to the
    real value (a hash of an altered container would break correlation).
    """

    def _entry(key: Any, value: Any) -> Any:
        if _check_field_rule(str(key).lower(), opts) is not None:
            return value
        if value not in ("", None) and _is_secret_key((key,), BODY_SECRET_KEYS):
            return _REDACTED
        return _redact_body_secrets(value, opts)

    if isinstance(node, dict):
        return {k: _entry(k, v) for k, v in node.items()}
    if _pii_is_pair_list(node):
        return [[k, _entry(k, v)] for k, v in node]
    if isinstance(node, (list, tuple, set, frozenset)):
        return [_redact_body_secrets(item, opts) for item in node]
    return node


def _key_untouched(key: str, rule_key: str, value: str, opts: _PiiOpts, depth: int = 0) -> bool:
    """True when no field rule or email mask acts on the key (either spelling).

    A key that embeds ``&`` / ``=`` is re-parsed as a query only when the key itself
    is unprotected: an operator rule naming it (drop/mask) or an email in it
    (``alice&bob@example.com``) must keep applying to the *original* key, not be
    bypassed by the inner pairs it would split into. A key that is itself a URL (or
    carries one) keeps the whole-key URL scrub for the same reason.
    """
    kept, sv = _apply_key_rules(key, rule_key, value, opts)
    return (
        kept
        and sv == value
        and _mask_emails(rule_key) == rule_key
        and _scrub_url_if_carries(key, opts, depth) is None
    )


def _scrub_query_string(query_string: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Apply the per-key PII rules to a raw url-encoded query string.

    Scrubs each ``(key, value)`` pair independently so repeated non-sensitive
    parameters (e.g. ``?tag=a&tag=b``) survive instead of being collapsed. After
    the key-based rules, unambiguous email tokens in the value are masked even
    under a non-sensitive key (e.g. ``q=alice@example.com``). ``parse_qsl``
    percent-decodes values, so encoded emails (``q=alice%40example.com``) are
    caught too. A value that is itself a URL / carries a nested query
    (``next=/search?phone=…``) is scrubbed as a URL so its inner parameters get
    the field rules too, instead of being re-emitted raw by ``urlencode``. An
    email in the *parameter name* (``?alice@example.com=1``) is masked as well —
    ``urlencode`` would otherwise re-emit the raw key. The field rules match the
    bounded-decoded key (:func:`_rule_key`): a pair whose key hides its own ``=`` /
    ``&`` (``phone%3D…``, ``x%26phone=…``) is re-parsed as a query, and a key still
    encoded at the decode cap has its pair redacted.
    """
    scrubbed: List[Tuple[str, Optional[str]]] = []
    for key, value in parse_qsl(query_string, keep_blank_values=True):
        rule_key = _rule_key(key)
        if rule_key is None:
            scrubbed.append((_REDACTED, _REDACTED))
            continue
        if ("?" in rule_key or "#" in rule_key) and _apply_key_rules(
            key, rule_key, value, opts
        ) == (True, value):  # an operator rule on the whole key (drop / mask) wins
            # A nested query lives *inside* the key (``?a:/search?phone=…`` splits at
            # the first ``=`` into key ``a:/search?phone``; ``%2Fp%3Fx%3Dy%26phone``
            # decodes to one): re-join and scrub the whole thing as a URL so the value
            # gets the field rules. Re-emitted as a bare key (no stray ``=``).
            text = f"{rule_key}={value}" if value else rule_key
            joined = _scrub_carried_url(text, opts, depth)
            if joined is not None and (joined != text or not value):
                scrubbed.append((joined, None))
                continue
        if ("=" in rule_key or "&" in rule_key) and _key_untouched(
            key, rule_key, value, opts, depth
        ):
            # Hidden pair structure. ``rule_key`` is fully decoded, so the re-parse
            # cannot find another layer; the value is re-quoted to stay as parsed.
            # A pair the rules leave alone (a bare base64 token ``?dG9rZW4%3D``)
            # falls through and is re-emitted as written.
            hidden = f"{rule_key}={quote_plus(value)}" if value else rule_key
            inner = parse_qsl(_scrub_query_string(hidden, opts, depth), keep_blank_values=True)
            if inner != parse_qsl(hidden, keep_blank_values=True):
                scrubbed.extend(inner)
                continue
        kept, masked = _apply_key_rules(key, rule_key, value, opts)
        for k, v in [(key, masked)] if kept else []:
            if isinstance(v, str):
                if v != value:
                    # An operator mask/hash rule matched. ``_mask_value`` leaves a
                    # partial email mask (``a***@example.com supersecret``), so
                    # redact the whole value wholesale in that case.
                    v = cast(str, _redact_partial_email_mask(value, v))
                else:
                    # No rule changed the value; recurse for a URL/nested query
                    # (a percent-encoded redirect is decoded first, see helper),
                    # otherwise mask any embedded email.
                    scrubbed_url = _scrub_url_if_carries(v, opts, depth)
                    v = scrubbed_url if scrubbed_url is not None else _mask_emails(v)
            # A parameter *name* can itself be a nested (percent-encoded) redirect
            # when the whole URL is used as a bare query key
            # (``?%252Fsearch%253Ftoken%253D…`` → ``parse_qsl`` yields it as an
            # empty-valued key). Give the key the same URL/nested-query scrub as a
            # value so its inner ``token=…`` isn't re-emitted decodable; otherwise
            # just mask any embedded email.
            scrubbed_key = _scrub_url_if_carries(k, opts, depth)
            if scrubbed_key is None:
                scrubbed_key = cast(str, _mask_emails(k))
            # A URL-shaped key stays a bare key (no ``=``), as the first pass emitted it.
            bare = not v and ("?" in scrubbed_key or "#" in scrubbed_key)
            scrubbed.append((scrubbed_key, None if bare else v))
    return "&".join(
        quote_plus(k) if v is None else f"{quote_plus(k)}={quote_plus(v)}" for k, v in scrubbed
    )


def _scrub_url(url: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Expose any hidden encoded URL structure, then scrub the URL.

    Exposing an attacker-hidden encoded query/fragment/authority (``%3F``/``%23``/
    ``%40``) is an invariant of URL scrubbing, so it happens here first — that way
    *every* caller (free text, generic leaves, URL fields, URL headers) is covered
    uniformly and no path can hand ``urlsplit`` a still-encoded URL and mistake a
    hidden query for path text (see :func:`_repair_url`).

    The query component is then masked per the field rules plus email-value masking;
    unambiguous email tokens embedded in the path (``/users/alice@example.com``,
    literal or percent-encoded ``%40``) are masked; a query-like or email-bearing
    fragment (``#access_token=…`` / ``#email=…``, possibly percent-encoded) is
    redacted wholesale while a plain anchor (``#section``) is kept. Userinfo in
    the authority (``https://user:secret@host/…``) is redacted wholesale — it can
    carry basic-auth credentials that neither the query nor field rules catch.

    ``urlsplit`` raises ``ValueError`` on malformed URLs (e.g. ``http://[``). Since
    this runs inside ``before_send`` — which must never raise — :func:`_split_repaired`
    repairs such a URL so ``urlsplit`` succeeds, the same component policy applies, and
    its query is redacted wholesale; a URL that can't be parsed even then is redacted.

    ``depth`` bounds the URL→query→nested-URL recursion: a query value that is
    itself a URL re-enters here one level deeper, so a maliciously nested
    ``next=/p?next=/p?…`` is redacted once :data:`_MAX_URL_NESTING` is exceeded
    rather than overflowing the stack inside ``before_send``.
    """
    if depth > _MAX_URL_NESTING:
        return _REDACTED
    form = _repair_url(url)
    if form is None:  # decode cap exhausted while still changing → don't leak
        return _REDACTED
    url = form
    split = _split_repaired(url)
    if split is None:  # unparseable even after repair → don't leak
        return _REDACTED
    parts, repaired = split
    netloc = parts.netloc
    if "@" in netloc:  # strip user:password@, keep host[:port] as written
        netloc = f"{_REDACTED}@{netloc.rsplit('@', 1)[1]}"
    elif "%" in netloc:  # encoded ``%40`` userinfo — decode only when encoded
        decoded_netloc, exhausted = _bounded_unquote(netloc)
        if exhausted:
            netloc = _REDACTED
        elif "@" in decoded_netloc:
            netloc = f"{_REDACTED}@{decoded_netloc.rsplit('@', 1)[1]}"
    if repaired and netloc != parts.netloc:
        # Credentials in an unparseable URL can't be stripped precisely: redact it all.
        return _REDACTED
    # Mask emails in the path, whether the ``@`` is literal or percent-encoded
    # (``/users/alice%40example.com``). Apply ``_mask_emails`` to the *encoded*
    # path so unrelated percent-encoding like ``%2F`` isn't decoded as a side
    # effect — a path like ``/users/alice%40example.com/files/a%2Fb`` keeps
    # its ``a%2Fb`` segment intact. ``redact_on_exhaust=False`` masks a deeply
    # encoded path email in place (via :func:`_mask_emails_basic`) instead of
    # redacting the whole path, redacting only when even that finds nothing.
    path = parts.path
    if _NESTED_USERINFO_RE.search(path):
        # A credentialed URL nested in the path (``/r/https://user:pw@10.0.0.5/p``),
        # or the whole value behind a non-URL prefix (``'https://user:pw@…``, which
        # ``urlsplit`` parses as all path): scrub each located URL as its own URL so
        # redacting its userinfo doesn't depend on the email grammar below (which
        # misses a numeric / non-domain host).
        pieces: List[str] = []
        pos = 0
        for span in find_url_spans(path, kinds=("absolute",)):
            nested = _scrub_url_token(path[span.start : span.end], opts, depth + 1)
            pieces.extend((path[pos : span.start], nested))
            pos = span.end
        path = "".join(pieces) + path[pos:]
    path = _mask_emails(path, redact_on_exhaust=False)
    query = parts.query
    if query:
        # A repaired (originally unparseable) URL has no trustworthy host, so its
        # query is redacted wholesale rather than field-scrubbed.
        query = _REDACTED if repaired else _scrub_query_string(query, opts, depth + 1)
    # Fragments can carry OAuth implicit-flow tokens / PII (``#access_token=…``,
    # ``#email=…``, possibly percent-encoded). Redact a query-like or
    # email-bearing fragment wholesale; leave plain anchors (``#section``) intact.
    fragment = parts.fragment
    if fragment and _fragment_carries_pii(fragment):
        fragment = _REDACTED
    if (
        netloc == parts.netloc
        and path == parts.path
        and query == parts.query
        and fragment == parts.fragment
    ):
        return url  # nothing to scrub — avoid reserialising the URL
    return urlunsplit(parts._replace(netloc=netloc, path=path, query=query, fragment=fragment))


def _scrub_body_str(raw: Any, opts: _PiiOpts) -> str:
    """Scrub a raw (unparsed) request body supplied as ``str``/``bytes``.

    Sentry allows ``request.data`` to be a raw body. JSON and url-encoded form
    bodies are parsed, scrubbed with the field rules, and re-serialised; anything
    else is redacted wholesale (this is the last client-side scrubber, so an
    unparseable body must not be shipped raw).
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    parsed = _redact_body_secrets(parsed, opts)
    if _is_pair_list(parsed):
        # A JSON body that decodes to [[key, value], …] is form data, not a
        # positional array: `sanitize_body` would treat it positionally and never
        # apply the key rules, so scrub it key-aware (matching the direct
        # `request.data` pair-list branch).
        scrubbed = _scrub_pair_list(parsed, _content_opts(opts))
        _scrub_value_fields(scrubbed, opts)
        return json.dumps(scrubbed)
    if isinstance(parsed, (dict, list)):
        # Strip the walk-managed keys from the body pass (URL/query/text keys are
        # applied once by the local `_scrub_value_fields` walk below), matching
        # how the top-level containers are scrubbed.
        scrubbed = _content_opts(opts).body(parsed)
        # request.data is re-serialised to a string here, so the top-level event
        # walk can't reach into it — apply the value walk to the parsed body now.
        _scrub_value_fields(scrubbed, opts)
        return json.dumps(scrubbed)
    if parsed is not None:
        # Valid JSON but a scalar (a quoted string / number / bool), not a
        # container. It is *not* a url-encoded form body, so don't reparse it as
        # one — a JSON string like ``"phone=0812345678"`` would otherwise have
        # its number re-emitted raw (the surrounding quotes break the key match).
        # There's no key to scrub by, so redact wholesale.
        return _REDACTED
    # url-encoded form body (``k=v&k=v``, no raw whitespace) — scrub per pair.
    if "=" in text and not any(ch.isspace() for ch in text):
        return _scrub_query_string(text, opts)
    return _REDACTED


def _scrub_request_body(data: Any, opts: _PiiOpts, content_opts: _PiiOpts) -> Any:
    """Scrub a ``request.data`` body of any shape the request block accepts.

    Dispatches on the body's shape so every form gets the right rules:

    - ``[[key, value], …]`` pair list → key-aware pair scrub (``sanitize_body``
      would treat it as a positional list and never apply the key rules);
    - ``dict`` / ``list`` → body scrub (drop/mask/hash by key name);
    - positional ``tuple`` (not a pair list) → normalised to a list first so
      ``sanitize_body`` recurses into its nested dicts/leaves instead of
      falling through to ``_scrub_body_str`` (which would stringify and redact
      the whole body);
    - ``str`` / ``bytes`` → parsed (JSON / url-encoded form) or redacted.

    ``content_opts`` (walk-managed keys stripped) is used for the container
    shapes so the URL/query/free-text field rules stay with the whole-event
    walk; the raw-string path uses full ``opts`` because it is re-serialised
    and the top-level walk can't reach into it.
    """
    if isinstance(data, (dict, list, tuple)):
        data = _redact_body_secrets(data, opts)
        if _is_pair_list(data):
            return _scrub_pair_list(data, content_opts)
        return content_opts.body(data)
    return _scrub_body_str(data, opts)


def _scrub_free_text_keyed_value(key_lower: str, value: Any, opts: _PiiOpts) -> Any:
    """Scrub one named entry inside a free-text subtree.

    Shared by the dict and list-of-pairs branches of :func:`_scrub_all_text` so
    both shapes are scrubbed identically. A ``db.statement`` /
    ``db.query.parameter.*`` bind value is redacted wholesale; a *string* value
    under a well-known URL/query/free-text key gets the key's registry handler;
    anything else is free-text-scrubbed recursively. Subtree keys (``params``)
    are deliberately not routed here — the subtree's operator rule was already
    applied by :func:`_scrub_text_subtree`, and a nested ``params`` just recurses.
    """
    if _is_statement_key(key_lower):
        return _REDACTED
    rule, dispatch_key = _walk_rule(key_lower)
    if isinstance(value, str) and rule is not None and not rule.subtree:
        return rule.handler(dispatch_key, value, opts)
    if (rule is not None and not rule.subtree) or _strip_bytes_repr_key(key_lower) != key_lower:
        # A compound value under a walk-managed key (the body pass stripped those
        # rules) or a colliding bytes-repr key the subtree body pass couldn't match: apply
        # the operator field rule via the unwrapped name, or the value leaks.
        field_rule = _check_field_rule(key_lower, opts)
        if field_rule == "hash":
            return _hash_value(str(value), opts.hash_salt)
        if field_rule in {"drop", "mask"}:
            return _REDACTED
    return _scrub_all_text(value, opts)


def _walk_named_entries(
    obj: Any, opts: _PiiOpts, entry: Callable[[str, Any, _PiiOpts], Any]
) -> None:
    """Iterate a dict or list-of-pairs container, applying ``entry`` to each value.

    Shared by :func:`_scrub_value_fields` and :func:`_scrub_all_text` so the
    container scaffolding (email-key masking, dedupe, in-place rebuild) never
    drifts between the general walk and the free-text walk. ``entry`` is
    ``(key_lower, value, opts) -> scrubbed_value``. Dicts are mutated in place
    (keys masked with dedupe), pair lists rewritten in place; a bare list is
    returned unchanged for the caller's positional branch.
    """
    if isinstance(obj, dict):
        rebuilt: Dict[Any, Any] = {}
        suffixes: Dict[str, int] = {}
        for key, value in list(obj.items()):
            masked_key = _dedupe_masked_key(rebuilt, _mask_email_key(key), suffixes)
            key_lower = str(masked_key).lower()
            rebuilt[masked_key] = entry(key_lower, value, opts)
        obj.clear()
        obj.update(rebuilt)
    elif _is_pair_list(obj):
        # A list-of-[key, value] pairs needs the same key-aware treatment as the
        # dict branch — otherwise a statement key like ``db.statement`` in
        # ``[["db.statement", "SELECT ..."]]`` would be treated as a positional
        # list and never redacted.
        for i, pair in enumerate(obj):
            key_lower = str(pair[0]).lower()
            masked_key = _mask_email_key(pair[0])
            obj[i] = [masked_key, entry(key_lower, pair[1], opts)]


def _scrub_all_text(obj: Any, opts: _PiiOpts) -> Any:
    """Scrub a free-text subtree, applying the key-based rules to named entries.

    Used for keys whose whole subtree is free text (``_TEXT_SUBTREE_KEYS``), so
    the caller reassigns for a bare string and mutates in place for dict/list.
    Callers pass a tuple/set-free structure (``opts.body`` normalises those).

    A *named* subtree entry (a ``logentry.params`` dict arg) can carry the same
    structured keys the whole-event walk recognises — a ``db.statement`` /
    ``db.query.parameter.*`` bind value, or a ``url`` / ``http.query`` — which
    ``opts.body`` doesn't know and free-text scrubbing alone would miss. Apply
    those redaction / URL-query rules by key here (via
    :func:`_scrub_free_text_keyed_value`), and pattern-scrub the remaining
    leaves (positional args like ``("alice@example.com",)``) as free text so
    their embedded emails/URLs are masked.
    """
    if isinstance(obj, str):
        return _scrub_text(obj, opts)
    if isinstance(obj, dict):
        _walk_named_entries(obj, opts, _scrub_free_text_keyed_value)
    elif _is_pair_list(obj):
        _walk_named_entries(obj, opts, _scrub_free_text_keyed_value)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            obj[i] = _scrub_all_text(item, opts)
    return obj


def _scrub_text_subtree(value: Any, opts: _PiiOpts) -> Any:
    """Scrub a free-text subtree (e.g. ``logentry.params``).

    Sentry's logging integration copies ``params`` straight from ``record.args``,
    so it can be a *tuple* of positional args (``logger.error("u=%s", email)``)
    or a *dict* of named args (``logger.error("u=%(u)s", {"u": email})``), and a
    named arg can itself be a tuple/set of dicts. :func:`_content_opts` +
    ``opts.body`` normalises tuple/set databags to lists at every depth and
    applies the key-based rules to dict args like ``{"phone": …}`` (masked/hashed
    by name) wherever they nest — with the walk-managed keys (``url`` /
    ``http.query`` / statements) stripped so :func:`_scrub_all_text` applies them
    exactly once — then pattern-scrubs the remaining string leaves.
    """
    # The key-based body pass already ran over every container that can hold a
    # ``params`` subtree (:func:`_scrub_metadata` covers ``logentry.params``), so
    # running it again here would hash/mask the same leaf twice.
    return _scrub_all_text(value, opts)


def _normalize_container(value: Any, opts: _PiiOpts) -> Any:
    """Apply the key-based rules to a tuple/set-shaped event value.

    A tuple- or set-shaped value the walk reaches without a prior ``opts.body``
    pass (e.g. ``set_extra("items", ({"phone": …},))`` or
    ``set_extra("cc", {"a@b.com"})``) needs one so keys like ``phone`` /
    ``authorization`` inside it are masked/dropped/hashed. ``opts.body``
    normalises the container to a list (see :func:`sanitize_body`) and applies
    those rules; the value walk then scrubs its string leaves. Non-container
    values are returned unchanged — their container already had ``opts.body``.

    The normalisation uses :func:`_content_opts` (walk-managed keys stripped): a
    ``url`` / ``message`` field inside the container is scrubbed *only* by the
    subsequent walk, so a tuple-contained ``url`` is hashed once — the same
    single ``sha256`` as ``request.url`` — not double-hashed here and in the walk.
    """
    if isinstance(value, (tuple, set, frozenset)):
        return _content_opts(opts).body(value)
    return value


def _scrub_url_or_query(key_lower: str, value: str, opts: _PiiOpts) -> str:
    rule = _check_field_rule(key_lower, opts)
    if rule == "hash":
        return _hash_value(value, opts.hash_salt)
    if rule is not None:
        return _REDACTED
    if key_lower in _URL_VALUE_KEYS:
        return _scrub_url(value, opts)  # _scrub_url exposes hidden structure itself
    query = value[1:] if value.startswith("?") else value
    return _scrub_query_string(query, opts)


def _scrub_fragment_value(key_lower: str, value: str, opts: _PiiOpts) -> str:
    rule = _check_field_rule(key_lower, opts)
    if rule == "hash":
        return _hash_value(value, opts.hash_salt)
    if rule is not None or _fragment_carries_pii(value):
        return _REDACTED
    return value


def _scrub_text_field(key_lower: str, value: str, opts: _PiiOpts) -> str:
    """Scrub a free-text field (``message`` / ``formatted`` / ``value``), but let
    an explicit operator field rule win.

    :func:`_scrub_text` only masks embedded emails/URLs, so a non-email secret
    (``token=supersecret``) in a top-level / breadcrumb / logentry message would
    survive an ``EXTRA_MASK_FIELDS={"message"}`` / drop / hash rule keyed by the
    field name. Apply that field rule first — an explicit *drop* redacts to
    ``[Filtered]`` (the walk can't delete a key mid-iteration, but the value is
    still removed) — and pattern-scrub as free text only when no rule matched.
    """
    sanitized = opts.mapping({key_lower: value})
    if key_lower not in sanitized:  # operator drop
        return _REDACTED
    if sanitized[key_lower] != value:  # operator mask / hash
        scrubbed = sanitized[key_lower]
        # ``_mask_value`` preserves everything after the first ``@``, so a
        # masked free-text field like ``alice@example.com token=supersecret``
        # becomes ``a***@example.com token=supersecret``, leaking the secret.
        # Redact the whole field instead of re-scrubbing the partial mask.
        if _is_sha256_hex(scrubbed):
            return scrubbed  # SHA256 hash — safe to return
        return _REDACTED
    return _scrub_text(value, opts)


def _is_statement_key(key_lower: str) -> bool:
    """True for DB/cache statement keys, whose value is redacted wholesale.

    Statement literals can't be parsed out, so the whole value is redacted
    (matching the db/cache span-*description* treatment). Covers the exact
    ``_STATEMENT_VALUE_KEYS`` plus the ``db.query.parameter.<name>`` bound-param
    prefix. A serialized byte key (``"b'db.statement'"``) is matched by its
    unwrapped name too.
    """
    if key_lower in _STATEMENT_VALUE_KEYS or key_lower.startswith(_STATEMENT_KEY_PREFIXES):
        return True
    stripped = _strip_bytes_repr_key(key_lower)
    return stripped != key_lower and (
        stripped in _STATEMENT_VALUE_KEYS or stripped.startswith(_STATEMENT_KEY_PREFIXES)
    )


@dataclass(frozen=True)
class _KeyRule:
    """How the whole-event walk rewrites a well-known key's value.

    ``handler`` has the signature ``(key_lower, value, opts) -> scrubbed``.
    ``subtree`` marks keys whose *entire* value subtree is free text (e.g.
    ``logentry.params``): the walk applies the operator field rule to the whole
    subtree first and otherwise scrubs its leaves, rather than treating the
    value as a single string leaf.
    """

    handler: Callable[[str, str, _PiiOpts], Any]
    subtree: bool = False


def _scrub_text_subtree_field(key_lower: str, value: Any, opts: _PiiOpts) -> Any:
    """Adapter matching a subtree scrubber to the ``_KeyRule.handler`` shape."""
    return _scrub_text_subtree(value, opts)


# Single registry of every well-known key the whole-event walk rewrites, mapped
# to its scrub handler. Extending it (a new OTel/Sentry semantic key, a new
# free-text key) automatically (1) strips the key from the pre-walk body/mapping
# passes via ``_WALK_MANAGED_KEYS`` below, (2) routes string values through the
# handler in :func:`_walk_keyed_value`, (3) covers non-string values under the
# key in the same function, and (4) teaches :func:`_scrub_all_text`
# for free-text subtrees — no other edit needed. Statement keys are handled
# separately by :func:`_is_statement_key` because their redaction is
# unconditional and ``db.query.parameter.*`` is a prefix, not a single key.
_WALK_RULES: Dict[str, _KeyRule] = {}
_WALK_RULES.update({key: _KeyRule(handler=_scrub_url_or_query) for key in _URL_VALUE_KEYS})
_WALK_RULES.update({key: _KeyRule(handler=_scrub_url_or_query) for key in _QUERY_VALUE_KEYS})
_WALK_RULES.update({key: _KeyRule(handler=_scrub_text_field) for key in _TEXT_VALUE_KEYS})
_WALK_RULES.update({key: _KeyRule(handler=_scrub_fragment_value) for key in _FRAGMENT_VALUE_KEYS})
_WALK_RULES.update(
    {key: _KeyRule(handler=_scrub_text_subtree_field, subtree=True) for key in _TEXT_SUBTREE_KEYS}
)

# Walk-managed keys are exactly the registry's keys, derived programmatically
# so the ``_content_opts`` stripping can never drift from the registry.
_WALK_MANAGED_KEYS = frozenset(_WALK_RULES)


def _walk_rule(key_lower: str) -> Tuple[Optional[_KeyRule], str]:
    """Look up a walk-managed rule, falling back to a key's unwrapped bytes name.

    A serialized byte key (``"b'url'"``) coexisting with its plain form is kept
    distinct by ``_normalize_bytes_leaves`` (no clobber); still route its value
    through the unwrapped key's registry handler. Returns ``(rule, key)`` where
    ``key`` is the name the registry matched on — the handler must dispatch on
    it (e.g. ``_scrub_url_or_query`` checks ``_URL_VALUE_KEYS`` membership), not
    the possibly-repr'd ``key_lower``.
    """
    rule = _WALK_RULES.get(key_lower)
    if rule is not None:
        return rule, key_lower
    stripped = _strip_bytes_repr_key(key_lower)
    if stripped != key_lower:
        unwrapped = _WALK_RULES.get(stripped)
        if unwrapped is not None:
            return unwrapped, stripped
    return None, key_lower


def _walk_keyed_value(key_lower: str, value: Any, opts: _PiiOpts) -> Any:
    """Apply the whole-event walk's keyed rules to one ``(key, value)`` entry.

    Shared by the dict, list-of-pairs and positional-list branches of
    :func:`_scrub_value_fields` (and by the subtree walk) so every shape is
    scrubbed identically:

    - a free-text-subtree key (``params``) → the operator field rule wins
      (drop/mask/hash the whole subtree), else the subtree's leaves are scrubbed;
    - a DB statement key → redacted wholesale regardless of value type;
    - a *string* value under a well-known key → the key's :data:`_WALK_RULES`
      handler; under any other key → the URL/query backstop
      (:func:`_scrub_url_if_carries`) then the email backstop (:func:`_mask_emails`);
    - a *non-string* value under a walk-managed key → the operator field rule is
      applied here (the body pass stripped these keys via ``_content_opts``, so a
      compound ``{"url": ["supersecret"]}`` with ``EXTRA_MASK_FIELDS={"url"}``
      must not fall through to recursion and never be redacted);
    - anything else → the container is normalised and recursively walked.
    """
    rule, dispatch_key = _walk_rule(key_lower)
    if rule is not None and rule.subtree:
        # An explicit operator rule for the whole subtree field (e.g.
        # EXTRA_DROP_HEADERS={"params"}) wins over pattern-scrubbing its leaves —
        # a positional ``params=["supersecret"]`` has no key or email/URL pattern
        # to catch otherwise. A drop redacts wholesale (the walk can't delete a
        # key mid-iteration); else scrub the subtree's leaves.
        # Resolved (unwrapped) name: a ``b'params'`` key must hit the ``params`` rule.
        sanitized = opts.mapping({dispatch_key: value})
        if dispatch_key not in sanitized:  # operator drop
            return _REDACTED
        if sanitized[dispatch_key] != value:  # operator mask / hash
            return _scrub_masked_field_result(dispatch_key, value, sanitized[dispatch_key], opts)
        return _scrub_text_subtree(value, opts)
    if _is_statement_key(key_lower):
        # Redact DB statements/bind params regardless of value type — an
        # array/IN-clause parameter (``{"db.query.parameter.x": [...]}``) must
        # not slip past the string-only branch into list recursion.
        return _REDACTED
    if isinstance(value, str):
        if rule is not None:
            return cast(str, rule.handler(dispatch_key, value, opts))
        if _strip_bytes_repr_key(key_lower) != key_lower:
            # A colliding bytes-repr key (``b'phone'`` beside ``phone``) the body
            # pass couldn't match: apply the operator field rule via the
            # unwrapped name here, or the value would reach Sentry raw.
            field_rule = _check_field_rule(key_lower, opts)
            if field_rule == "hash":
                return _hash_value(value, opts.hash_salt)
            if field_rule in {"drop", "mask"}:
                return _REDACTED
        # A string under a non-walk-managed key: the URL/query backstop for a
        # value that carries a nested URL (``next=/search?…``), then the email
        # backstop for an email under a non-sensitive key.
        scrubbed_url = _scrub_url_if_carries(value, opts)
        if scrubbed_url is not None:
            return scrubbed_url
        return cast(str, _mask_emails(value))
    if rule is not None or _strip_bytes_repr_key(key_lower) != key_lower:
        # Non-string value under a walk-managed (or colliding bytes-repr) key — the
        # body pass deliberately
        # removed these keys via ``_content_opts``, so the operator field rule is
        # applied here (see docstring).
        field_rule = _check_field_rule(key_lower, opts)
        if field_rule in {"drop", "mask"}:
            return _REDACTED
        if field_rule == "hash":
            return _hash_value(str(value), opts.hash_salt)
    value = _normalize_container(value, opts)
    _scrub_value_fields(value, opts)
    return value


def _scrub_value_fields(obj: Any, opts: _PiiOpts) -> None:
    """Recursively scrub string values stored under well-known keys, in place.

    The key-based body/mapping scrubbers only drop/mask/hash by field *name*, so
    a value that is itself a URL, a raw query string, a DB statement, or free
    text — wherever it nests in the event — passes through verbatim. This single
    walk rewrites those, matching keys case-insensitively:

    - URL keys (``_URL_VALUE_KEYS``) via :func:`_scrub_url`;
    - query-string keys (``_QUERY_VALUE_KEYS``) via :func:`_scrub_query_string`;
    - statement keys (``_STATEMENT_VALUE_KEYS``) redacted wholesale;
    - free-text keys (``_TEXT_VALUE_KEYS``) via :func:`_scrub_text`;
    - free-text subtrees (``_TEXT_SUBTREE_KEYS``, e.g. ``logentry.params``) via
      :func:`_scrub_text_subtree`;
    - **any other string leaf** gets the email backstop (:func:`_mask_emails`),
      so an email under a non-sensitive key (``tags``, ``extra``, ``contexts``,
      header/env values, …) is masked just like one in a query value.

    All of these are driven by the single :data:`_WALK_RULES` registry via
    :func:`_walk_keyed_value`, so both the dict and list-of-pairs shapes are
    scrubbed identically and adding a new PII-carrying key is a one-line
    registry extension rather than a new bespoke walker.
    """
    if isinstance(obj, dict):
        _walk_named_entries(obj, opts, _walk_keyed_value)
    elif _is_pair_list(obj):
        # A list-of-[key, value] pairs (Sentry form data / a set_extra pair list):
        # the walk is the single authority for the URL/query/free-text field rules
        # (the drop/mask/hash-by-name rules already ran in the body/pair pass), so
        # apply them here by each pair's key via the shared entry helper — the
        # positional branch below would only see two bare string leaves and mask
        # emails.
        _walk_named_entries(obj, opts, _walk_keyed_value)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                # A bare (positional) list element carries no key, but a
                # URL-looking one (``["/search?phone=…"]``) must still have its
                # query scrubbed like a dict/pair value — `_walk_keyed_value`
                # with an empty key falls through to the URL / email backstop.
                obj[i] = _walk_keyed_value("", item, opts)
            else:
                item = _normalize_container(item, opts)
                obj[i] = item
                _scrub_value_fields(item, opts)


def _decode_key(key: Any, original: Dict[Any, Any]) -> Any:
    """Normalise one dict key like :func:`_normalize_bytes_leaves` (decode / unwrap repr)."""
    if isinstance(key, (bytes, bytearray)):
        return _decode_bytes_leaf(key)
    if isinstance(key, str):
        new_key = _strip_bytes_repr_key(key)
        return key if new_key != key and new_key in original else new_key
    return key


def _pair_shape(node: List[Any]) -> int:
    """Classify a list: 2 = canonical pair list, 1 = pair list only after decoding, 0 = other."""
    if not node:
        return 0
    canonical = _pii_is_pair_list(node) and all(isinstance(i, list) for i in node)
    if canonical:
        return 2
    if _pii_is_pair_list(node):
        return 1  # tuple pairs: keep the shape-exact reference path
    if all(
        isinstance(i, (list, tuple)) and len(i) == 2 and isinstance(i[0], (str, bytes, bytearray))
        for i in node
    ):
        return 1  # bytes pair keys become a pair list only after decoding
    return 0


def _fused_reference(node: Any, bopts: _PiiOpts, opts: _PiiOpts) -> Any:
    """Reference (multi-pass) sequence the fused walk is defined to equal.

    Used for the rare shapes (tuples/sets, bytes-keyed pairs, walk-managed or
    byte-repr keys) where the multi-pass semantics are subtle; those subtrees are
    small and the common containers stay on the single-visit path.
    """
    node = bopts.body(_normalize_bytes_leaves(node))
    _scrub_value_fields(node, opts)
    return node


def _fused_child(
    key: Any, raw: Any, walk_key_lower: str, bopts: _PiiOpts, opts: _PiiOpts
) -> Tuple[bool, Any]:
    """Body (field-rule) + value-walk for one entry; returns ``(keep, scrubbed)``."""
    key_lower = str(key).lower()
    drop, mask, hsh = bopts.rule_sets
    if key_lower in drop:
        return False, None
    rule, _ = _walk_rule(walk_key_lower)
    if rule is not None or _strip_bytes_repr_key(walk_key_lower) != walk_key_lower:
        decoded = _normalize_bytes_leaves(raw)
        value = bopts.body({key: decoded})[key]
        return True, _walk_keyed_value(walk_key_lower, value, opts)
    if _is_statement_key(walk_key_lower):
        return True, _REDACTED
    if key_lower in mask:
        if isinstance(raw, (bytes, bytearray)):
            raw = _decode_bytes_leaf(raw)
        return True, _walk_keyed_value(
            walk_key_lower, _mask_value(raw) if isinstance(raw, str) else "***", opts
        )
    if bopts.level == PiiLevel.SENSITIVE and key_lower in hsh:
        hashed = _hash_value(str(_normalize_bytes_leaves(raw)), bopts.hash_salt)
        return True, _walk_keyed_value(walk_key_lower, hashed, opts)
    if isinstance(raw, (dict, list)):
        return True, _fused_walk(raw, bopts, opts)
    if isinstance(raw, (bytes, bytearray)):
        return True, _walk_keyed_value(walk_key_lower, _decode_bytes_leaf(raw), opts)
    if isinstance(raw, (tuple, set, frozenset)):
        value = bopts.body(_normalize_bytes_leaves(raw))
        return True, _walk_keyed_value(walk_key_lower, value, opts)
    return True, _walk_keyed_value(walk_key_lower, raw, opts)


def _fused_walk(node: Any, bopts: _PiiOpts, opts: _PiiOpts) -> Any:
    """Single visit-once scrub of a ``dict`` / ``list`` subtree (returns a new container).

    Equals ``_scrub_value_fields(bopts.body(_normalize_bytes_leaves(node)), opts)``
    — bytes decoding, the key-based drop/mask/hash rules and the URL / query /
    text value rules — but each node is visited once, with no intermediate
    copies, and a value is never rule-hashed twice. Container values the multi-pass
    pipeline treats specially go through :func:`_fused_reference`.
    """
    if isinstance(node, dict):
        norm: Dict[Any, Any] = {}
        for key, value in node.items():
            norm[_decode_key(key, node)] = value
        rebuilt: Dict[Any, Any] = {}
        suffixes: Dict[str, int] = {}
        for key, raw in norm.items():
            masked_key = _dedupe_masked_key(rebuilt, _mask_email_key(key), suffixes)
            keep, value = _fused_child(key, raw, str(masked_key).lower(), bopts, opts)
            if keep:
                rebuilt[masked_key] = value
        return rebuilt
    shape = _pair_shape(node)
    if shape == 1:
        return _fused_reference(node, bopts, opts)
    out: List[Any] = []
    if shape == 2:
        for key, raw in node:
            keep, value = _fused_child(key, raw, str(key).lower(), bopts, opts)
            if keep:
                out.append([_mask_email_key(key), value])
        return out
    for item in node:
        if isinstance(item, (dict, list)):
            out.append(_fused_walk(item, bopts, opts))
        elif isinstance(item, str):
            out.append(_walk_keyed_value("", item, opts))
        elif isinstance(item, (bytes, bytearray)):
            out.append(_walk_keyed_value("", _decode_bytes_leaf(item), opts))
        elif isinstance(item, (tuple, set, frozenset)):
            out.append(_fused_reference(item, bopts, opts))
        else:
            out.append(item)
    return out


# ``_is_pair_list`` delegates to the canonical definition in
# ``observe_kit.pii_rules``. The shared version accepts both ``list`` and
# ``tuple`` nodes (Sentry can supply either), while the old sentry-local copy
# only accepted ``list`` — tuple-shaped pairs would have bypassed key-based
# scrubbing. The ``TypeGuard`` wrapper gives mypy narrowing at call sites;
# the runtime behavior comes from ``pii_rules``.
def _is_pair_list(value: Any) -> TypeGuard[List[Any]]:
    return _pii_is_pair_list(value)


def _scrub_pair_list(pairs: List[Any], opts: _PiiOpts) -> List[Any]:
    """Sanitize a Sentry list-of-``[key, value]`` field (headers / query string).

    Applies the drop/mask/hash rules by each pair's key (``DROP_HEADERS`` entries
    like ``Authorization`` are removed; repeated keys are preserved). The
    URL / query / free-text field rules and structural scrubbing for a pair value
    (``["url", "/search?phone=…"]``, ``["http.path", …]``, a ``next=…`` redirect)
    are applied by the whole-event :func:`_scrub_value_fields` walk, which is the
    single authority for those keys — so ``opts`` here should have those keys
    stripped (see :func:`_content_opts`) to avoid applying the rule twice.
    Non-pair items are left untouched.
    """
    out: List[Any] = []
    for item in pairs:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            key, value = item
            sanitized = opts.mapping({str(key): value})
            if str(key) not in sanitized:  # dropped headers are omitted
                continue
            value = sanitized[str(key)]
            # If the mapping left the value untouched (the outer pair key was not
            # in any drop/mask/hash set), its nested keys still need the body
            # rules — e.g. ``["profile", {"phone": "0812345678"}]`` where
            # ``profile`` is a non-sensitive wrapper but ``phone`` is a mask rule
            # target. ``sanitize_mapping`` never recurses, so body-scrub the
            # value now; ``opts.body`` is idempotent when no rule matches.
            if isinstance(value, (dict, list, tuple, set, frozenset)):
                value = opts.body(value)
            out.append([key, value])
        else:
            out.append(item)
    return out


def _apply_env_rule(
    env: Dict[str, Any],
    key: str,
    value: str,
    opts: _PiiOpts,
    on_no_match: Callable[[str, _PiiOpts], str],
) -> None:
    rule = _check_field_rule(key.lower(), opts)
    if rule == "drop":
        del env[key]
    elif rule == "mask":
        env[key] = _REDACTED
    elif rule == "hash":
        env[key] = _hash_value(value, opts.hash_salt)
    else:
        env[key] = on_no_match(value, opts)


def _scrub_env(env: Dict[str, Any], opts: _PiiOpts) -> None:
    """Scrub CGI/WSGI ``request.env`` entries (Django ``META``) in place.

    Sentry can populate ``request.env`` from the raw WSGI environ, where request
    headers appear CGI-normalised (``HTTP_AUTHORIZATION``, ``HTTP_COOKIE``,
    ``HTTP_X_FORWARDED_FOR`` …) alongside a raw ``QUERY_STRING`` and URI mirrors
    (``REQUEST_URI`` / ``RAW_URI`` / ``PATH_INFO``) — none of which the
    header/query-string branches above ever see. Map each ``HTTP_*`` key back
    to its header name and apply the same drop/mask/hash rules (plus URL-query
    scrubbing for URL-valued headers like ``HTTP_REFERER``). For ``QUERY_STRING``
    and the URI mirrors, an explicit operator rule keyed by the lower-cased env
    name (e.g. ``EXTRA_DROP_HEADERS={"path_info"}``) is applied first and only
    the unhandled remainder is query/URL-scrubbed (see
    :func:`_scrub_special_env_value`). ``*_COOKIE`` env keys (Django's
    ``CSRF_COOKIE`` secret) are redacted wholesale like ``request.cookies``.
    Other non-``HTTP_`` WSGI keys (``REMOTE_USER``, a custom ``SESSION_KEY`` …)
    still honour the operator's ``EXTRA_MASK_FIELDS`` / ``EXTRA_DROP_HEADERS``
    matched by the lower-cased key name. Client-IP entries (``REMOTE_ADDR`` and
    forwarded-IP headers) are hashed by :func:`_hash_ip_fields` at SENSITIVE.

    This is the env's *single* scrubbing pass: custom (non-allowlisted) headers
    that carry URL-looking values (``HTTP_X_NEXT: /search?phone=…``) are also
    URL/query-scrubbed here, so no later pass re-iterates the env (and the
    QUERY_STRING / URI-mirror keys don't need to be temporarily popped to avoid
    double-hashing).
    """
    for key in list(env):
        value = env[key]
        if not isinstance(value, str) or not value:
            continue
        if key == "QUERY_STRING":
            _apply_env_rule(env, key, value, opts, _scrub_query_string)
            continue
        if key in _URI_ENV_KEYS:  # REQUEST_URI / RAW_URI / PATH_INFO mirror the URL
            # ``_scrub_url`` exposes hidden encoded delimiters itself, so a
            # percent-encoded mirror like ``%2Fsearch%3Fphone%3D0812345678`` still
            # has its ``?``/``&`` structure revealed and the phone scrubbed. These
            # keys never reach the generic header branch below, so this is their
            # only (and single) chance to scrub.
            _apply_env_rule(env, key, value, opts, _scrub_url)
            continue
        if not isinstance(key, str):
            # Non-string env key (integer index, …) — skip to avoid
            # ``cgi_header_name`` raising ``AttributeError``.
            continue
        name = cgi_header_name(key)
        if name is None:
            # Cookie/secret env keys that aren't ``HTTP_COOKIE`` — Django's CSRF
            # middleware exposes the raw secret as ``request.META["CSRF_COOKIE"]``.
            # Cookies are redacted wholesale (see ``request.cookies``) and
            # ``csrf_cookie`` isn't in the built-in drop set, so redact any
            # ``*_COOKIE`` env value before the generic field mapping.
            if key.upper().endswith("_COOKIE"):
                env[key] = _REDACTED
                continue
            # Non-HTTP_ WSGI key (REMOTE_USER, a custom SESSION_KEY, REMOTE_ADDR,
            # …). Honour operator EXTRA_MASK_FIELDS / EXTRA_DROP_HEADERS matched
            # by the lower-cased key name.
            lowered = key.lower()
            if key == "REMOTE_ADDR":
                # Honour a drop/mask rule keyed by the DB name ("remote_addr") or
                # the semantic alias ("ip") — but *not* the hash: the client IP is
                # hashed once by _hash_ip_fields (SENSITIVE), so exclude both
                # names from the hash set here (``ip`` is a default HASH_FIELD).
                drop, mask, hsh = opts.rule_sets
                sanitized = sanitize_mapping(
                    {lowered: value},
                    opts.level,
                    drop,
                    mask,
                    hsh - {"remote_addr", "ip"},
                    opts.hash_salt,
                    aliases={lowered: "ip"},
                )
            else:
                sanitized = opts.mapping({lowered: value})
            if lowered not in sanitized:  # operator-dropped key
                del env[key]
            else:
                env[key] = _scrub_custom_url_header(lowered, sanitized[lowered], opts)
            continue
        sanitized = opts.mapping({name: value})
        if name not in sanitized:  # dropped header (authorization, cookie, …)
            del env[key]
            continue
        new_value = sanitized[name]
        if name in _URL_HEADER_NAMES and isinstance(new_value, str) and new_value:
            new_value = _scrub_url_header_value(name, new_value, opts)
        elif isinstance(new_value, str) and new_value:
            # A custom (non-allowlisted) header that carries a URL-looking value
            # (``HTTP_X_NEXT: /search?phone=…``) isn't covered by
            # ``_scrub_url_header_value`` (allowlist-only). Scrubbing it here —
            # during the single env pass — removes the separate
            # ``_scrub_url_values`` pass and its QUERY_STRING/URI pop-restore.
            new_value = _scrub_custom_url_header(name, new_value, opts)
        env[key] = new_value


def _scrub_url_header_value(name_lower: str, value: str, opts: _PiiOpts) -> str:
    """Scrub a URL-valued header, honouring an explicit mask rule wholesale.

    ``opts.mapping`` may already have partially masked the value via
    ``_mask_value`` (``https://a:pw@host/reset/secret`` → ``h***@host/reset/secret``),
    which corrupts the authority so the URL scrub can no longer strip the
    credentials / path secret. When a mask rule matched the header name, redact
    the whole value instead; a hash rule's digest is left untouched.

    A URL-valued header (``Referer``, …) or CGI env entry (``HTTP_REFERER``) can
    also hide encoded URL delimiters (``%3F``/``%23``/``%40``); ``_scrub_url``
    exposes those itself before parsing, so no separate decode step is needed.
    """
    if _check_field_rule(name_lower, opts) == "mask":
        return _REDACTED
    return _scrub_url(value, opts)


def _scrub_url_headers(headers: Any, opts: _PiiOpts) -> None:
    """Scrub the query of URL-valued request headers (``Referer``, …) in place.

    The key-based header rules pass these through unchanged and the value walk
    doesn't recognise header names, so a header like
    ``Referer: https://h/p?email=x`` would keep its query PII. Handles both the
    dict and Sentry list-of-``[key, value]`` header forms.
    """
    if isinstance(headers, dict):
        for key in headers:
            value = headers[key]
            name = str(key).lower()
            if name in _URL_HEADER_NAMES and isinstance(value, str) and value:
                headers[key] = _scrub_url_header_value(name, value, opts)
    elif isinstance(headers, (list, tuple)):
        for i, pair in enumerate(headers):
            if (
                isinstance(pair, (list, tuple))
                and len(pair) == 2
                and str(pair[0]).lower() in _URL_HEADER_NAMES
                and isinstance(pair[1], str)
                and pair[1]
            ):
                name = str(pair[0]).lower()
                _set_pair_value(headers, i, _scrub_url_header_value(name, pair[1], opts))


def _scrub_generic_string_keys(
    container: Dict[str, Any], opts: _PiiOpts, skip: FrozenSet[str] = frozenset()
) -> None:
    """Apply the operator drop/mask/hash-by-name rules to plain string entries.

    Covers generic keys no body/mapping pass owns (``server_name``, ``release``,
    breadcrumb ``category``, ...). ``opts`` must be the content opts so the
    walk-managed keys (rewritten once by the final walk) are never touched
    here; ``skip`` excludes keys another phase already owns (``transaction``).
    One application per entry: the walk's backstop leaves hashes/redactions as-is.
    """
    for key in list(container):
        value = container[key]
        if not isinstance(key, str) or not isinstance(value, str) or key.lower() in skip:
            continue
        sanitized = opts.mapping({key: value})
        if key in sanitized:
            container[key] = sanitized[key]
        else:
            del container[key]


def _scrub_breadcrumbs(breadcrumbs: Any, opts: _PiiOpts) -> None:
    """Apply the key-based field rules to each breadcrumb's ``data`` payload.

    URL / free-text values (including the breadcrumb ``message``) are handled by
    the top-level :func:`_scrub_value_fields` walk; this only adds the
    drop/mask/hash-by-name rules that walk doesn't do.
    """
    if isinstance(breadcrumbs, dict):
        values = breadcrumbs.get("values")
    elif isinstance(breadcrumbs, list):
        values = breadcrumbs
    else:
        return
    if not isinstance(values, list):
        return
    for crumb in values:
        if not isinstance(crumb, dict):
            continue
        if isinstance(crumb.get("data"), (dict, list)):
            crumb["data"] = opts.body(crumb["data"])
        _scrub_generic_string_keys(crumb, opts)


class _UrlLayout:
    """Component offsets of one visible absolute URL inside ``text`` (computed once)."""

    def __init__(self, text: str, start: int, end: int) -> None:
        scheme_end = text.find("//", start, end)
        auth = start if scheme_end < 0 else scheme_end + 2
        found = (text.find(ch, auth, end) for ch in "/?#")
        self.auth_end = min((i for i in found if i >= 0), default=end)
        frag = text.find("#", self.auth_end, end)
        query = text.find("?", self.auth_end, end)
        if query >= 0 and (frag < 0 or query < frag):
            self.path_end = self.query_start = query
            self.query_end = end if frag < 0 else frag
        else:
            self.path_end = end if frag < 0 else frag
            self.query_start = -1
            self.query_end = self.path_end
        # Offsets of the ``&`` separators inside the query, for pair boundaries.
        self._amps: List[int] = []
        if self.query_start >= 0:
            pos = text.find("&", self.query_start, self.query_end)
            while pos >= 0:
                self._amps.append(pos)
                pos = text.find("&", pos + 1, self.query_end)

    def pair_start(self, pos: int) -> int:
        i = bisect_right(self._amps, pos - 1) - 1  # last ``&`` strictly before ``pos``
        return self._amps[i] + 1 if i >= 0 else self.query_start + 1

    def pair_end(self, pos: int) -> int:
        i = bisect_right(self._amps, pos - 1)  # first ``&`` at or after ``pos``
        return self._amps[i] if i < len(self._amps) else self.query_end


def _hides_nested_authority(part: str) -> bool:
    """True when a percent-encoded path slice decodes (bounded) to a nested ``//``."""
    decoded, _ = _bounded_unquote(part)
    return "//" in decoded


def _scrub_hidden_encoded_urls(
    text: str, opts: _PiiOpts, depth: int = 0, owned: Sequence[Tuple[int, int]] = ()
) -> str:
    """Scrub percent-encoded URLs / nested queries hidden in free text.

    A *visible* URL in a leaf must not shadow an *encoded* redirect elsewhere in
    the same text (``https://safe.test then %252Fsearch%253Fphone%253D…``) — the
    visible-URL path sends mixed prose here via :func:`_scrub_text`, and the plain
    URL locator passes only see already-decoded structure, so the
    encoded portion would reach Sentry unchanged.

    Only the encoded-URL *substring* (``find_encoded_url_spans``, anchored on an
    encoded ``/``/``:``/``?``) is bounded-decoded and scrubbed, so the surrounding
    text is preserved verbatim — a ``redirect:`` prefix, a trailing ``,hello%20world``,
    or an adjacent ``progress=100%25`` keep their original encoding. Each candidate
    is scrubbed only when its fully decoded form actually carries a URL; if the
    structural detector sees a URL the scrubber can't isolate, the candidate is
    redacted rather than restored (never leak a recoverable encoded secret).
    """
    if "%" not in text:
        return text

    # Spans of every *visible* absolute URL the URL locator recognizes in this
    # text (both ``scheme://…`` and scheme-relative ``//…`` forms). An encoded
    # slice that falls inside one of these belongs to the absolute-URL pass, which
    # scrubs the whole URL as a unit; anything outside is a genuinely hidden slice
    # we own.
    all_spans = find_url_spans(text)
    url_spans = [(s.start, s.end) for s in all_spans if s.kind == "absolute"]
    # Non-absolute located URLs (relative / rootless): the located-URL pass scrubs
    # them as a unit, so a candidate inside one must not be whitespace-tokenised.
    rel_spans = [(s.start, s.end) for s in all_spans if s.kind != "absolute"]
    rel_starts = [a for a, _ in rel_spans]
    # ``owned``: sorted disjoint ranges an earlier pass already scrubbed.
    owned_starts = [a for a, _ in owned]

    def _inside(spans: Sequence[Tuple[int, int]], starts: List[int], a: int, b: int) -> bool:
        i = bisect_right(starts, a) - 1
        return i >= 0 and b <= spans[i][1]

    span_starts = [start for start, _ in url_spans]
    layouts: Dict[int, "_UrlLayout"] = {}
    handled_ranges: Dict[Tuple[int, int], bool] = {}

    def _layout(idx: int) -> "_UrlLayout":
        # Component boundaries and ``&`` offsets of one visible URL, computed once
        # per span (not per candidate) so many candidates in one URL stay linear.
        if idx not in layouts:
            layouts[idx] = _UrlLayout(text, *url_spans[idx])
        return layouts[idx]

    def _covered_components_handled(idx: int, start: int, end: int) -> bool:
        # True when the outer absolute-URL scrub handles *every* component the
        # candidate ``[start, end)`` covers: the authority (decoded and redacted by
        # ``_scrub_url``), the path (only if it hides no nested ``//`` authority —
        # ``_scrub_url`` does not expose one), each query key/value
        # (``_outer_scrub_handles``) and the fragment (redacted wholesale).
        lay = _layout(idx)
        if lay.auth_end <= start < lay.path_end:
            return False  # starts in the path: the pass owns it
        if start < lay.path_end and end > lay.auth_end:
            path_part = text[max(start, lay.auth_end) : min(end, lay.path_end)]
            if "%" in path_part and _hides_nested_authority(path_part):
                return False
        if lay.query_start >= 0 and end > lay.query_start + 1 and start < lay.query_end:
            lo = max(start, lay.query_start + 1)
            pair_start = lay.pair_start(lo)
            pair_end = lay.pair_end(min(end, lay.query_end))
            key = (pair_start, pair_end)
            if key not in handled_ranges:
                handled_ranges[key] = all(
                    _outer_scrub_handles(part)
                    for pair in text[pair_start:pair_end].split("&")
                    for part in pair.partition("=")[::2]
                    if _hides_url_structure(part)
                )
            return handled_ranges[key]
        return True

    def _repl(start: int, end: int) -> str:
        # An encoded slice inside a *visible* URL is left for the absolute-URL
        # scrub, which scrubs the whole URL as a unit — redacting a deeply-encoded
        # authority (``https://user%253A…%2540host``) wholesale instead of fragmenting
        # it into a surviving ``https://<username>`` prefix, and avoiding re-scrubbing
        # a value ``_scrub_url`` already scrubbed (which would e.g. hash a nested
        # ``EXTRA_HASH_FIELDS`` token twice). That holds only when the candidate lies
        # *fully inside* the located span (its character class admits ``'`` etc. that
        # end the span, so an out-of-span tail must be scrubbed here) and the outer
        # scrub handles every component the candidate covers
        # (:func:`_covered_components_handled`) — a candidate can begin in the
        # authority and run on into a path holding a nested authority
        # (``https://u%3Ap%40host/https%3A%2F%2Falice%3As%40dest/x``).
        candidate = text[start:end]
        if _inside(owned, owned_starts, start, end):
            return candidate  # scrubbed once already; a second pass would re-hash
        idx = bisect_right(span_starts, start) - 1
        if idx >= 0 and start < url_spans[idx][1] and end <= url_spans[idx][1]:
            if _covered_components_handled(idx, start, end):
                return candidate
            auth_end = _layout(idx).auth_end
            if start < auth_end < end:
                # Begins in the authority but runs on into components the outer scrub
                # can't handle: leave only the authority part (which the outer scrub
                # redacts as a unit; scrubbing it here would strand the username) and
                # scrub the rest in this pass.
                head = candidate[: auth_end - start]
                return head + _scrub_candidate(candidate[auth_end - start :], "keep")
            return _scrub_candidate(candidate, "keep")
        return _scrub_candidate(
            candidate, "quote" if _inside(rel_spans, rel_starts, start, end) else "split"
        )

    def _scrub_candidate(candidate: str, ws: str = "split") -> str:
        decoded, exhausted = _bounded_unquote(candidate)
        if decoded == candidate:
            return candidate
        if exhausted:
            return _REDACTED
        # Encoded whitespace (``%3Fphone%3D…%20HTTP/1.1``) glues prose onto the URL
        # token, which then isn't recognised as a URL as a whole: scrub per token.
        # Inside a visible URL the outer pass must keep the query boundaries, so a
        # decoded space may not precede an unscrubbed ``&k=v``: an absolute URL is
        # left whole (``ws="keep"``), a relative one is scrubbed per token with the
        # whitespace re-encoded (``ws="quote"``).
        requote = ws == "quote"
        if ws != "keep" and any(c.isspace() for c in decoded):
            scrubbed: Optional[str] = _scrub_whitespace_tokens(decoded, opts, depth, requote)
            if requote:
                decoded = _quote_whitespace(decoded)
        else:
            scrubbed = _scrub_carried_url(decoded, opts, depth)
        if scrubbed is not None:
            # ``scrubbed == decoded`` means the URL was isolated but contained
            # no values requiring scrubbing — keep the original candidate rather
            # than redacting it, so benign encoded URLs (``%2Fdocs%3Fpage%3D1``)
            # aren't turned into ``[Filtered]`` while their visible equivalent
            # is preserved.
            return scrubbed if scrubbed != decoded else candidate
        return candidate

    out: List[str] = []
    pos = 0
    for start, end in find_encoded_url_spans(text):
        out.extend((text[pos:start], _repl(start, end)))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _whitespace_runs(text: str) -> List[str]:
    """Split ``text`` into alternating whitespace / non-whitespace runs (lossless)."""
    return ["".join(run) for _, run in groupby(text, str.isspace)]


def _quote_whitespace(text: str) -> str:
    return "".join(quote(p, safe="") if p[0].isspace() else p for p in _whitespace_runs(text))


def _scrub_whitespace_tokens(text: str, opts: _PiiOpts, depth: int, requote: bool = False) -> str:
    """Scrub each whitespace-separated token of ``text`` that is / carries a URL.

    ``requote`` re-encodes the whitespace runs (for a candidate inside a visible URL).
    """
    parts = _whitespace_runs(text)
    for i, token in enumerate(parts):
        if token[0].isspace():
            parts[i] = quote(token, safe="") if requote else token
            continue
        scrubbed = _scrub_carried_url(token, opts, depth)
        if scrubbed is not None:
            parts[i] = scrubbed
    return "".join(parts)


def _scrub_prefixed_urls(
    text: str, opts: _PiiOpts, depth: int
) -> Tuple[str, List[Tuple[int, int]]]:
    """Scrub ``prefix:/path?key=value`` relative URLs (``redirect:/search?phone=…``).

    The relative-URL locator rejects a ``/`` that follows ``:`` (it would otherwise
    match inside ``scheme://``), so such a URL is invisible to
    :func:`_scrub_located_urls`. Re-locate on a same-length copy of the text whose
    ``:`` before a single ``/`` is blanked; a span overlapping a URL the locator
    already sees in the real text was scrubbed by that pass and is left alone.
    """
    chunks: List[str] = []
    pos = 0
    colon = text.find(":/")
    while colon >= 0:
        if not text.startswith("//", colon + 1):
            chunks.extend((text[pos:colon], " "))
            pos = colon + 1
        colon = text.find(":/", colon + 1)
    if not chunks:
        return text, []
    chunks.append(text[pos:])
    seen = find_url_spans(text)
    seen_starts = [span.start for span in seen]
    out: List[str] = []
    done: List[Tuple[int, int]] = []  # output ranges of the URLs scrubbed here
    pos = 0
    written = 0
    for span in find_url_spans("".join(chunks), kinds=("relative",)):
        i = bisect_right(seen_starts, span.end - 1) - 1  # last seen span starting before the end
        if i >= 0 and seen[i].end > span.start:
            continue
        new = _scrub_url(text[span.start : span.end], opts, depth)
        out.extend((text[pos : span.start], new))
        written += span.start - pos
        done.append((written, written + len(new)))
        written += len(new)
        pos = span.end
    out.append(text[pos:])
    return "".join(out), done


def _scrub_located_urls(text: str, opts: _PiiOpts, depth: int) -> str:
    """Scrub every URL the locator finds; absolute spans may hold adjacent URLs.

    One pass per kind (absolute, relative, rootless), each locating on the text the
    previous pass already rewrote — not one combined pass over the original text,
    which would drop a relative span overlapping an absolute one instead of
    scrubbing what is left of it after the absolute scrub.
    """
    done: List[Tuple[int, int]] = []  # ranges of already-scrubbed absolute URLs
    for kind in ALL_KINDS:
        out: List[str] = []
        pos = 0
        # A later-kind match wholly inside an already-scrubbed absolute URL (the
        # rootless ``com/p?ip=…`` tail after a hostname dot) must not be scrubbed
        # again — its query values were handled once, and hashing twice would make
        # the pseudonym depend on which field carried the URL.
        starts = [r[0] for r in done]
        rewrites: List[Tuple[int, int]] = []  # (old end, length delta), in text order
        written = 0  # length of ``out`` so far
        fresh: List[Tuple[int, int]] = []
        for span in find_url_spans(text, kinds=(kind,)):
            i = bisect_right(starts, span.start) - 1
            if i >= 0 and done[i][1] >= span.end:
                continue
            out.append(text[pos : span.start])
            written += span.start - pos
            piece = text[span.start : span.end]
            new = (
                _scrub_url_token(piece, opts, depth)
                if kind == "absolute"
                else _scrub_url(piece, opts, depth)
            )
            out.append(new)
            fresh.append((written, written + len(new)))
            written += len(new)
            rewrites.append((span.end, len(new) - (span.end - span.start)))
            pos = span.end
        out.append(text[pos:])
        text = "".join(out)
        # ponytail: ranges straddling a rewrite are shifted by whole-rewrite deltas
        # only; fine for the "skip already-scrubbed" heuristic.
        ends = [r[0] for r in rewrites]
        cum = list(accumulate(r[1] for r in rewrites))

        def shift(x: int) -> int:
            k = bisect_right(ends, x)
            return x + (cum[k - 1] if k else 0)

        done = [(shift(s), shift(e)) for s, e in done]
        if kind == "absolute":
            done = fresh
    return text


# What ends a ``?query`` run in free text (besides whitespace): the next ``?`` / ``#``
# and the prose separators the URL locators stop at.
_TEXT_QUERY_RUN_STOPS = frozenset("?#,;|)]}<>\"'")


def _scrub_encoded_pair_queries(text: str, opts: _PiiOpts, depth: int) -> str:
    """Scrub free-text ``?query`` runs whose ``=`` is percent-encoded (``s?phone%3D…``).

    The URL locators only recognise a query with a literal ``=``, so such a run is
    never located. Runs after the located-URL pass and only touches a run with no
    literal ``=`` (so nothing already scrubbed is scrubbed twice); a run the field
    rules leave alone is kept verbatim. Linear: runs are disjoint.
    """
    if "%" not in text:
        return text
    out: List[str] = []
    pos = 0
    start = text.find("?")
    while start >= 0:
        end = start + 1
        while end < len(text) and not (text[end].isspace() or text[end] in _TEXT_QUERY_RUN_STOPS):
            end += 1
        run = text[start + 1 : end]
        decoded, exhausted = _bounded_unquote(run) if "=" not in run else (run, False)
        if decoded != run and (exhausted or "=" in decoded):
            new = _scrub_query_string(run, opts, depth)
            if parse_qsl(new, keep_blank_values=True) != parse_qsl(run, keep_blank_values=True):
                out += (text[pos : start + 1], new)
                pos = end
        start = text.find("?", end)
    out.append(text[pos:])
    return "".join(out)


def _scrub_text(text: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Pattern-scrub PII out of free-text (message/log) fields.

    Structured field rules don't apply to free text, so scrub the concrete,
    unambiguous vectors: embedded ``http(s)`` URLs *and* relative URLs carrying a
    query (``GET /search?phone=…`` and rootless ``callback?phone=…`` — the same
    query the field rules mask when the URL is absolute or stored in
    ``request.url``), plus email addresses — literal or percent-encoded — via
    :func:`_mask_emails`. Other patterns (phone/SSN) are too false-positive-prone
    to detect in bare prose and are left alone, mirroring the key-based model.
    """
    # Prefixed first: the encoded pass emits decoded ``redirect:/s?tok=...`` text that
    # a later prefixed pass would scrub (and hash) a second time.
    text, owned = _scrub_prefixed_urls(text, opts, depth)
    text = _scrub_hidden_encoded_urls(text, opts, depth, owned)
    text = _scrub_located_urls(text, opts, depth)
    text = _scrub_encoded_pair_queries(text, opts, depth)
    return cast(str, _mask_emails(text))


def _scrub_span_description(description: str, op: Any, opts: _PiiOpts) -> str:
    """Scrub a span ``description``.

    DB/cache spans carry a free-form statement whose literals can't be parsed
    out, so redact them wholesale. Other descriptions are free text (an http
    span's ``GET https://...`` line, or arbitrary custom text like
    ``processing alice@example.com``), so run them through :func:`_scrub_text`
    to mask both embedded URLs and email addresses — ``description`` is not a
    ``_TEXT_VALUE_KEYS`` key, so the whole-event walk won't reach it. An explicit
    operator rule keyed by ``description`` (``EXTRA_MASK_FIELDS={"description"}``
    / drop / hash) wins first, so a non-email secret (``token=…``) that the
    free-text scrub alone would keep is masked/redacted/hashed.
    """
    if isinstance(op, str) and op.split(".", 1)[0] in {"db", "cache"}:
        return _REDACTED
    sanitized = opts.mapping({"description": description})
    if "description" not in sanitized:  # operator drop
        return _REDACTED
    if sanitized["description"] != description:  # operator mask / hash
        scrubbed = sanitized["description"]
        if _is_sha256_hex(scrubbed):
            return scrubbed
        return _REDACTED
    return _scrub_text(description, opts)


def _scrub_spans(event: Dict[str, Any], opts: _PiiOpts, full_opts: _PiiOpts) -> None:
    """Scrub ``data``, ``tags`` and ``description`` on each span (transaction events)."""
    spans = event.get("spans")
    if not isinstance(spans, list):
        return
    for span in spans:
        if not isinstance(span, dict):
            continue
        if isinstance(span.get("data"), (dict, list)):
            span["data"] = opts.body(span["data"])
        # ``span.set_tag("phone", …)`` / a credential under ``authorization``
        # serializes into ``spans[*].tags``; the whole-event walk only masks
        # email patterns there, so apply the drop/mask/hash field rules here
        # (same shapes and opts as the top-level ``tags`` block).
        tags = span.get("tags")
        if isinstance(tags, dict):
            span["tags"] = opts.mapping(tags)
        elif isinstance(tags, (list, tuple)):
            span["tags"] = _scrub_pair_list(list(tags) if isinstance(tags, tuple) else tags, opts)
        description = span.get("description")
        if isinstance(description, str) and description:
            # Full options: descriptions are shielded from the later value walk, so
            # walk-managed operator rules (``message`` / ``url`` …) apply only here.
            span["description"] = _scrub_span_description(description, span.get("op"), full_opts)
        # Generic string keys (``op``, ``status``, ``origin``, ...): the operator
        # drop/mask/hash-by-name rules. Runs *after* the description scrub, which
        # classifies db/cache spans by the raw ``op`` — masking ``op`` first would
        # let a db statement escape wholesale redaction. ``description`` has its owner above.
        _scrub_generic_string_keys(span, opts, frozenset({"description"}))


def _scrub_stackframe_vars(event: Dict[str, Any], opts: _PiiOpts) -> None:
    """Scrub captured stack-local variables in exception/thread stacktraces."""
    for container_key in ("exception", "threads"):
        container = event.get(container_key)
        if not isinstance(container, dict):
            continue
        values = container.get("values")
        if not isinstance(values, list):
            continue
        for entry in values:
            if isinstance(entry, dict):
                _scrub_generic_string_keys(entry, opts)
            stacktrace = entry.get("stacktrace") if isinstance(entry, dict) else None
            frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
            if not isinstance(frames, list):
                continue
            for frame in frames:
                if isinstance(frame, dict):
                    _scrub_generic_string_keys(frame, opts)
                if isinstance(frame, dict) and isinstance(frame.get("vars"), (dict, list)):
                    frame["vars"] = opts.body(frame["vars"])


def _hash_ip_headers(request: Dict[str, Any], hash_salt: str, masked: FrozenSet[str]) -> None:
    """Hash proxy-forwarded client-IP headers (dict or list-of-pairs form).

    Skip a header the operator explicitly masked (its name is in ``masked``) so
    the requested mask isn't overwritten by a hash of the masked string.
    """
    headers = request.get("headers")
    if isinstance(headers, dict):
        for key in list(headers):
            name = str(key).lower()
            if name in _IP_HEADER_NAMES and headers[key] and name not in masked:
                headers[key] = _hash_value(str(headers[key]), hash_salt)
    elif isinstance(headers, list):
        rebuilt: List[Any] = []
        for pair in headers:
            if (
                isinstance(pair, (list, tuple))
                and len(pair) == 2
                and str(pair[0]).lower() in _IP_HEADER_NAMES
                and pair[1]
                and str(pair[0]).lower() not in masked
            ):
                rebuilt.append([pair[0], _hash_value(str(pair[1]), hash_salt)])
            else:
                rebuilt.append(pair)
        request["headers"] = rebuilt


def _hash_ip_fields(event: Dict[str, Any], hash_salt: str, masked: FrozenSet[str]) -> None:
    """Hash the client IP wherever Sentry records it (SENSITIVE level only).

    A field whose name the operator explicitly masked (in ``masked``, i.e.
    ``EXTRA_MASK_FIELDS``) is left as-is — an explicit mask wins over the IP
    hash, otherwise Sentry would receive a SHA-256 of the masked string instead
    of the operator-requested mask.
    """
    request = event.get("request")
    if isinstance(request, dict):
        env = request.get("env")
        if isinstance(env, dict):
            # REMOTE_ADDR plus any forwarded-IP header in CGI form
            # (HTTP_X_FORWARDED_FOR, HTTP_X_REAL_IP, …).
            for key in list(env):
                if not env.get(key):
                    continue
                if not isinstance(key, str):
                    continue
                name = "remote_addr" if key == "REMOTE_ADDR" else cgi_header_name(key)
                if name != "remote_addr" and name not in _IP_HEADER_NAMES:
                    continue  # not a client-IP field
                # An explicit mask keyed by the DB name *or* the ``ip`` alias
                # (REMOTE_ADDR only) wins over the IP hash.
                mask_names = {name, "ip"} if name == "remote_addr" else {name}
                if not (mask_names & masked):
                    env[key] = _hash_value(str(env[key]), hash_salt)
        _hash_ip_headers(request, hash_salt, masked)
    user = event.get("user")
    if isinstance(user, dict) and user.get("ip_address") and "ip_address" not in masked:
        user["ip_address"] = _hash_value(str(user["ip_address"]), hash_salt)


def _scrub_structured_pair(key: Any, value: Any, opts: _PiiOpts) -> Optional[Tuple[str, str]]:
    """``(key', value')`` when a dict / pair-list param key hides query structure and the
    query scrub changed it (``{"phone=0812345678": ""}``, ``{"%2Fp%3Fx%3Dy%26phone": …}``).

    The entry is run through :func:`_scrub_query_string` as the query it stands for, so
    the key gets exactly the string-form treatment; a pair that scrubs to itself is left
    alone (``None``). When the result splits into several pairs they are folded into
    the key (a dict entry cannot become several).
    """
    if not (isinstance(key, str) and isinstance(value, str)):
        return None
    if "%" not in key and not any(c in key for c in "?#&="):
        return None
    query = f"{quote_plus(key)}={quote_plus(value)}"
    pairs = parse_qsl(_scrub_query_string(query, opts), keep_blank_values=True)
    if pairs == parse_qsl(query, keep_blank_values=True):
        return None
    if len(pairs) == 1:
        return pairs[0]
    return "&".join(f"{k}={v}" for k, v in pairs), ""


def _rekey_param(key: Any, value: Any, opts: _PiiOpts) -> Tuple[Any, Any, bool]:
    """Apply :func:`_scrub_structured_pair` to one dict / pair-list entry.

    Returns ``(key', value', done)``; ``done`` means the field rules already ran inside
    the query scrub, so the caller must not apply them again (a hash would be hashed
    twice). An entry whose *original* key an operator rule acts on is left alone
    (``done=False``) so the whole-key drop / mask / hash wins. A list value cannot be
    re-split, so each element that changes is redacted (fail closed) and the key takes
    the scrubbed spelling.
    """
    if isinstance(key, str):
        rule_key = _rule_key(key)
        probe = value if not isinstance(value, (list, tuple)) else ""
        if rule_key is not None and _apply_key_rules(key, rule_key, probe, opts) != (True, probe):
            return key, value, False
    if isinstance(value, (list, tuple)) and not _is_pair_list(value):
        new_key, items = key, []
        for item in value:
            changed = None if item == _REDACTED else _scrub_structured_pair(key, item, opts)
            if changed:
                new_key, item = changed[0], _REDACTED
            items.append(item)
        return new_key, items, False
    changed = _scrub_structured_pair(key, value, opts)
    return (changed[0], changed[1], True) if changed else (key, value, False)


def _scrub_single_param(key: str, value: Any, opts: _PiiOpts) -> Optional[Any]:
    """Apply field rules to a single query param value, returning ``None`` if dropped.

    Handles both the key-level rules (drop/mask/hash) and the URL-value backstop
    (a value that is itself a URL or carries a nested query gets its inner params
    scrubbed). Used by the dict and list-of-pairs ``query_string`` branches to
    avoid calling ``sanitize_mapping`` on a list-valued dict entry (which would
    stringify the list and mask the repr, leaking individual elements).
    """
    if not isinstance(key, Hashable):  # malformed pair: must not raise in before_send
        key = str(key)
    rule_key = _rule_key(key) if isinstance(key, str) else key
    if rule_key is None:
        return _REDACTED
    kept, sv = _apply_key_rules(key, rule_key, value, opts)
    if not kept:
        return None
    if sv != value:  # a mask/hash rule matched — redact a partial email mask
        return _redact_partial_email_mask(value, sv)
    if isinstance(value, (dict, list, tuple)):
        # Nested containers need the field rules *and* the value walk: the saved
        # ``query_string`` is shielded from the later whole-event walk.
        nested = _content_opts(opts).body(value)
        _scrub_value_fields(nested, opts)
        return nested
    if isinstance(value, str):
        scrubbed_url = _scrub_url_if_carries(value, opts)
        if scrubbed_url is not None:
            return scrubbed_url
    return sv


def _decode_bytes_leaf(value: Any) -> str:
    """Decode a bytes leaf the way Sentry's serializer will (UTF-8, replace).

    Sentry stringifies ``bytes`` / ``bytearray`` leaves *after* ``before_send``
    runs, so a leaf like ``b"alice@example.com"`` would otherwise reach Sentry as
    the plain string ``alice@example.com`` having bypassed every ``str``-gated
    scrubber. Decoding here — with the same UTF-8/replace Sentry uses — lets the
    normal email / URL / field scrubbing see the exact text Sentry would emit.
    """
    return bytes(value).decode("utf-8", "replace")


def _strip_bytes_repr_key(key: str) -> str:
    """Unwrap a bytes-repr string key produced for a pre-serialized byte key.

    Sentry SDK serializes the event *before* invoking ``before_send``, so a
    nested byte key like ``b"phone"`` arrives as the string ``"b'phone'"`` — too
    late for the ``_decode_bytes_leaf`` pass to see the original bytes, and no
    longer matching the ``phone`` field rule. Recognise the ``b'...'`` /
    ``b"..."`` repr and unwrap it so the rule still matches. Only a well-formed
    repr is unwrapped; a literal ``b'phone'`` string key is indistinguishable by
    design, but such keys are vanishingly rare compared to the leak this guards.
    """
    if key.startswith("b'") and key.endswith("'") and len(key) >= 4:
        return key[2:-1]
    if key.startswith('b"') and key.endswith('"') and len(key) >= 4:
        return key[2:-1]
    return key


def _normalize_bytes_leaves(obj: Any) -> Any:
    """Recursively decode ``bytes`` / ``bytearray`` leaves (and dict keys) to str.

    Root-cause guard against a whole class of bypass: every scrubber gates on
    ``isinstance(_, str)``, but Sentry serializes byte-like leaves to strings only
    *after* ``before_send``, so any ``bytes`` value in ``extra`` / ``contexts`` /
    ``tags`` / structured query data / request bodies / message fields slips past
    scrubbing and is emitted raw. A single normalization pass at the start of
    :func:`scrub_event` converts them up front so the existing ``str`` scrubbers
    cover them uniformly. Container types are preserved (dict/list mutated in
    place, tuple/set rebuilt) so the downstream shape-specific handling — pair
    lists, positional tuples, set databags — is unchanged.

    Dict keys that already arrived as a bytes *repr* string (``"b'phone'"``,
    because Sentry serialized before ``before_send``) are unwrapped too via
    :func:`_strip_bytes_repr_key`, so the field rules still match.
    """
    if isinstance(obj, (bytes, bytearray)):
        return _decode_bytes_leaf(obj)
    if isinstance(obj, dict):
        rebuilt: Dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, (bytes, bytearray)):
                new_key = _decode_bytes_leaf(key)
            elif isinstance(key, str):
                new_key = _strip_bytes_repr_key(key)
                if new_key != key and new_key in obj:
                    new_key = key  # a plain key already exists — keep both distinct
            else:
                new_key = key
            rebuilt[new_key] = _normalize_bytes_leaves(value)
        obj.clear()
        obj.update(rebuilt)
        return obj
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            obj[i] = _normalize_bytes_leaves(item)
        return obj
    if isinstance(obj, tuple):
        return tuple(_normalize_bytes_leaves(item) for item in obj)
    if isinstance(obj, frozenset):
        return frozenset(_normalize_bytes_leaves(item) for item in obj)
    if isinstance(obj, set):
        return {_normalize_bytes_leaves(item) for item in obj}
    return obj


def _build_opts(
    level: PiiLevel,
    hash_salt: str,
    extra_drop: Optional[FrozenSet[str]],
    extra_mask: Optional[FrozenSet[str]],
    extra_hash: Optional[FrozenSet[str]],
) -> Tuple[_PiiOpts, _PiiOpts, _PiiOpts]:
    """Build the three PII-opt variants threaded through the scrub phases.

    Returns ``(opts, header_opts, content_opts)``:

    - ``opts`` — the full per-sink settings, with the semantic ``ip`` alias
      expanded to every concrete proxy-IP header name (so an operator rule keyed
      by ``ip`` applies to ``X-Forwarded-For`` / ``X-Real-IP`` / … too);
    - ``header_opts`` — ``opts`` minus the proxy-IP header names from
      ``extra_hash``: ``_hash_ip_fields`` hashes those once at SENSITIVE, so
      dropping them from the header/env mapping passes prevents a second hash of
      the same digest;
    - ``content_opts`` — ``opts`` with the walk-managed keys stripped from the
      operator sets (see :func:`_content_opts`), for the pre-walk body/mapping
      passes over user content.
    """
    opts = _PiiOpts(level, hash_salt, extra_drop, extra_mask, extra_hash)

    # Expand the semantic "ip" alias to cover all concrete proxy-IP header
    # names (X-Forwarded-For, X-Real-IP, …) — EXTRA_DROP_HEADERS/MASK/HASH
    # keyed by "ip" must apply to every forwarded-IP header too, not only to
    # REMOTE_ADDR (env) and user.ip_address (user object). The per-subsystem
    # expansions for those two are kept as well so the intent is explicit in
    # all downstream checks.
    for attr in ("extra_drop", "extra_mask", "extra_hash"):
        current = getattr(opts, attr, None)
        if current and "ip" in current:
            expanded = frozenset(current | _IP_HEADER_NAMES)
            if expanded != current:
                kwargs: dict[str, frozenset[str]] = {attr: expanded}
                opts = replace(opts, **kwargs)  # type: ignore[arg-type]

    # Forwarded-IP headers are hashed once by `_hash_ip_fields` (SENSITIVE) to
    # stay consistent with `REMOTE_ADDR` and the other sinks' `sha256(salt+ip)`.
    # If the operator also lists one in EXTRA_HASH_FIELDS, drop it from the
    # header/env mapping passes so the digest isn't hashed a second time.
    header_opts = opts
    if opts.extra_hash and opts.extra_hash & _IP_HEADER_NAMES:
        header_opts = replace(opts, extra_hash=frozenset(opts.extra_hash - _IP_HEADER_NAMES))

    # Opts for the body/mapping passes over user content (extra, contexts, data
    # dict, tags dict, span/breadcrumb data, frame vars, user). The final value
    # walk is the single authority for URL/query/free-text field rules, so these
    # keys are stripped here to avoid applying the rule twice (see `_content_opts`).
    content_opts = _content_opts(opts)

    return opts, header_opts, content_opts


def _apply_bytes_key_rules(mapping: Dict[Any, Any], opts: _PiiOpts) -> None:
    """Apply field rules, in place, to serialized byte-repr keys (``"b'authorization'"``).

    The mapping scrubbers match the plain key name only, and the request headers /
    query string are shielded from the later walk that handles the alias.
    """
    for key in list(mapping):
        if not isinstance(key, str) or _strip_bytes_repr_key(key.lower()) == key.lower():
            continue
        rule = _check_field_rule(key.lower(), opts)
        if rule == "drop":
            del mapping[key]
        elif rule == "mask":
            mapping[key] = _REDACTED
        elif rule == "hash":
            mapping[key] = _hash_value(str(mapping[key]), opts.hash_salt)


def _scrub_request(
    event: Dict[str, Any], opts: _PiiOpts, header_opts: _PiiOpts, content_opts: _PiiOpts
) -> None:
    """Scrub the ``request`` object — headers, cookies, fragment, query, body, env."""
    request = event.get("request")
    if not isinstance(request, dict):
        return
    headers = request.get("headers")
    if isinstance(headers, dict):
        _apply_bytes_key_rules(headers, header_opts)
        request["headers"] = header_opts.mapping(headers)
    elif isinstance(headers, (list, tuple)):
        request["headers"] = _scrub_pair_list(list(headers), header_opts)
    # Generic string keys (``method``, ``api_target``, ...): operator rules by name.
    # The pieces handled below (query_string / fragment / cookies / data) own theirs.
    _scrub_generic_string_keys(
        request, content_opts, frozenset({"query_string", "fragment", "cookies", "data"})
    )
    # URL-valued headers (Referer, …) keep query PII the key rules pass through.
    _scrub_url_headers(request.get("headers"), opts)
    # Redact cookies wholesale regardless of shape (dict / raw string / list).
    if request.get("cookies"):
        request["cookies"] = _REDACTED
    # A standalone ``request.fragment`` isn't reached by the URL scrub (only
    # the ``#…`` embedded in ``request.url`` is). Redact a query-like or
    # email-bearing fragment (OAuth ``access_token=…`` / ``email=…``) the same
    # way ``_scrub_url`` treats the fragment component; keep a plain anchor.
    fragment = request.get("fragment")
    if isinstance(fragment, str) and fragment:
        action, result = _check_value_rule("fragment", fragment, opts)
        if action == "drop":
            request.pop("fragment", None)
        elif action is not None:
            request["fragment"] = result
        elif _fragment_carries_pii(fragment):
            request["fragment"] = _REDACTED
    query_string = request.get("query_string")
    if isinstance(query_string, str) and query_string:
        action, result = _check_value_rule("query_string", query_string, opts)
        if action == "drop":
            request.pop("query_string", None)
        elif action is not None:
            request["query_string"] = result
        else:
            request["query_string"] = _scrub_query_string(query_string, opts)
    elif isinstance(query_string, (dict, list, tuple)) and query_string:
        action, result = _check_value_rule("query_string", str(query_string), opts)
        if action == "drop":
            request.pop("query_string", None)
        elif action is not None:
            request["query_string"] = result
        elif isinstance(query_string, dict):
            _apply_bytes_key_rules(query_string, opts)
            # Apply the field rules to each entry. A value that is a list
            # (repeated params like ``?token=a&token=b``) has each element
            # scrubbed individually via ``_scrub_single_param`` instead of
            # passing the whole list through ``sanitize_mapping`` (which
            # stringifies it and masks the repr, leaking individual elements
            # that survive ``_mask_value``).
            cleaned: Dict[str, Any] = {}
            for qk, orig in list(query_string.items()):
                qk, orig, done = _rekey_param(qk, orig, opts)
                if done:
                    if qk or orig:  # every inner pair dropped: nothing left to emit
                        cleaned[qk] = orig
                elif isinstance(orig, (list, tuple)) and not _is_pair_list(orig):
                    scrubbed_list: List[Any] = []
                    for v in orig:
                        sv = _scrub_single_param(qk, v, opts)
                        if sv is not None:
                            scrubbed_list.append(sv)
                    if scrubbed_list:
                        cleaned[qk] = scrubbed_list
                else:
                    sv = _scrub_single_param(qk, orig, opts)
                    if sv is not None:
                        cleaned[qk] = sv
            request["query_string"] = cleaned
        else:
            # List-of-pairs form. Handle list-valued values elementwise
            # (same as the dict branch) so repeated params like
            # [["token", ["x@y", "supersecret"]]] don't get stringified.
            cleaned_pairs: List[Any] = []
            qs_list = list(query_string) if isinstance(query_string, tuple) else query_string
            for pair in qs_list:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    k, v, done = _rekey_param(pair[0], pair[1], opts)
                    if done:
                        if k or v:
                            cleaned_pairs.append([k, v])
                    elif isinstance(v, (list, tuple)) and not _is_pair_list(v):
                        scrubbed_vals: List[Any] = []
                        for val in v:
                            sv = _scrub_single_param(k, val, opts)
                            if sv is not None:
                                scrubbed_vals.append(sv)
                        if scrubbed_vals:
                            cleaned_pairs.append([k, scrubbed_vals])
                    else:
                        sv = _scrub_single_param(k, v, opts)
                        if sv is not None:
                            cleaned_pairs.append([k, sv])
                else:
                    cleaned_pairs.append(pair)
            request["query_string"] = cleaned_pairs
    data = request.get("data")
    if (
        _is_pair_list(data)
        or isinstance(data, (dict, list, tuple))
        or (isinstance(data, (str, bytes)) and data)
    ):
        action, result = _check_value_rule("data", str(data), opts)
        if action == "drop":
            request.pop("data", None)
        elif action is not None:
            request["data"] = result
        else:
            request["data"] = _scrub_request_body(data, opts, content_opts)
    # CGI/WSGI env (Django META): HTTP_* headers + QUERY_STRING.
    env = request.get("env")
    if isinstance(env, dict):
        _scrub_env(env, header_opts)
    event["request"] = request


def _user_body_opts(content_opts: _PiiOpts) -> _PiiOpts:
    """Field-rule opts for the ``user`` object (``ip`` alias; ``ip_address`` left to the hasher)."""
    user_opts = content_opts
    # Honor the semantic "ip" alias for user.ip_address (matching _scrub_env's
    # alias for REMOTE_ADDR) — EXTRA_DROP_HEADERS/MASK/HASH keyed by "ip"
    # must apply to user.ip_address too, not only to env.REMOTE_ADDR.
    for attr in ("extra_drop", "extra_mask", "extra_hash"):
        current = getattr(user_opts, attr, None)
        if current and "ip" in current and "ip_address" not in current:
            user_kwargs: dict[str, frozenset[str]] = {attr: frozenset(current | {"ip_address"})}
            user_opts = replace(user_opts, **user_kwargs)  # type: ignore[arg-type]
    # `_hash_ip_fields` owns `user.ip_address` (single sha256, matching
    # REMOTE_ADDR), so drop it from the generic hash set here to avoid a
    # double-hash when the operator lists `ip_address` (or `ip` which was
    # expanded above) in EXTRA_HASH_FIELDS.
    if user_opts.extra_hash and "ip_address" in user_opts.extra_hash:
        user_opts = replace(user_opts, extra_hash=frozenset(user_opts.extra_hash - {"ip_address"}))
    return user_opts


def _scrub_metadata(event: Dict[str, Any], opts: _PiiOpts, content_opts: _PiiOpts) -> None:
    """Scrub top-level ``tags`` / ``extra`` / ``contexts`` / ``user``."""
    # Generic top-level string keys (``server_name``, ``release``, ``environment``,
    # ``logger``, ...) get the operator field rules; ``transaction`` is owned by
    # ``_scrub_trace_metadata`` and the walk-managed keys by the final walk.
    _scrub_generic_string_keys(event, content_opts, frozenset({"transaction"}))

    # Top-level tags (sentry_sdk.set_tag) are flat key→value pairs, in either
    # the dict or list-of-pairs shape.
    tags = event.get("tags")
    if isinstance(tags, dict):
        event["tags"] = content_opts.mapping(tags)
    elif isinstance(tags, (list, tuple)):
        event["tags"] = _scrub_pair_list(
            list(tags) if isinstance(tags, tuple) else tags, content_opts
        )

    extra = event.get("extra")
    if isinstance(extra, (dict, list)):
        event["extra"] = content_opts.body(extra)

    # ``logentry.params`` is the one ``params`` subtree no other phase covers; give
    # it its key-based body pass here so the walk applies only free-text scrubbing.
    logentry = event.get("logentry")
    if isinstance(logentry, dict) and "params" in logentry:
        logentry["params"] = content_opts.body(logentry["params"])

    # Application-attached contexts (sentry_sdk.set_context) are user-supplied
    # dicts just like extra, so apply the same field rules.
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        event["contexts"] = content_opts.body(contexts)

    # The user object can carry PII (email/username). Apply the field rules
    # (e.g. email masking) before the SENSITIVE ip_address hashing below.
    # `_hash_ip_fields` owns `user.ip_address` (single sha256, matching
    # REMOTE_ADDR), so drop it from the generic hash set here to avoid a
    # double-hash when the operator lists `ip_address` in EXTRA_HASH_FIELDS.
    user = event.get("user")
    if isinstance(user, dict):
        user_opts = _user_body_opts(content_opts)
        event["user"] = user_opts.body(user)


def _scrub_trace_metadata(event: Dict[str, Any], opts: _PiiOpts, content_opts: _PiiOpts) -> None:
    """Scrub trace/error metadata — breadcrumbs, transaction name, spans, frame vars."""
    _scrub_breadcrumbs(event.get("breadcrumbs"), content_opts)

    # Transaction events (traces) carry spans; the transaction name may embed a
    # query. Error events may carry captured stack-local variables. Both can
    # hold URLs / DB params / locals like `email`.
    transaction = event.get("transaction")
    if isinstance(transaction, str) and transaction:
        action, result = _check_value_rule("transaction", transaction, opts)
        if action == "drop":
            event.pop("transaction", None)
        elif action is not None:
            event["transaction"] = result
        else:
            # Brush the transaction name as free text — _scrub_text masks
            # embedded emails/URLs (and their query PII) via the URL locator.
            event["transaction"] = _scrub_text(transaction, opts)
    _scrub_spans(event, content_opts, opts)
    _scrub_stackframe_vars(event, content_opts)


def _walk_with_saved_fields(event: Dict[str, Any], opts: _PiiOpts) -> None:
    """Run the whole-event value walk, shielding already-scrubbed fields.

    The transaction name, span descriptions, and the fully scrubbed request
    fields (raw ``data`` string, ``query_string``, ``env``, ``headers``) were
    already URL/query-scrubbed by the earlier phases. The walk would re-detect
    their scrubbed URL/query strings as URLs and re-run ``_scrub_url`` over
    them — corrupting a serialised JSON body into invalid JSON *and* hashing an
    already-hashed nested param a second time (``token=sha256(sha256(secret))``),
    desyncing it from the single-hash ``request.url``. So they are temporarily
    popped, the walk runs, and each is restored with only the idempotent email
    backstop. A dict/list ``request.data`` stays in the walk (it still needs
    URL/query scrubbing and isn't misparsed).

    Custom *request headers* that carry URL-looking values (``X-Next:
    /search?phone=…``) are not covered by the allowlist ``_scrub_url_headers``
    (which only handles well-known URL headers like ``Referer``). Their URL/query
    scrub runs now, before the save, because the restore path only does the
    email backstop. The ``env`` needs no such pass: ``_scrub_env`` already
    scrubbed its custom URL-carrying headers in the single env pass.
    """
    # The transaction name and span descriptions were already URL/query-scrubbed
    # above — save them from the walk (same pattern as request fields below).
    saved_txn: Optional[str] = None
    if "transaction" in event and isinstance(event["transaction"], str):
        saved_txn = cast(str, event.pop("transaction"))
    saved_span_descs: List[Tuple[int, str]] = []
    existing_spans = event.get("spans")
    if isinstance(existing_spans, list):
        for si, span in enumerate(existing_spans):
            if isinstance(span, dict) and "description" in span:
                saved_span_descs.append((si, cast(str, span.pop("description"))))

    request_obj = event.get("request")
    saved_request: Dict[str, Any] = {}
    if isinstance(request_obj, dict):
        for rk in ("data", "query_string", "env", "headers"):
            if rk not in request_obj:
                continue
            if rk == "data" and not isinstance(request_obj["data"], str):
                continue  # dict/list body still needs the walk
            if rk == "headers":
                _scrub_url_values(request_obj[rk], opts)
            saved_request[rk] = request_obj.pop(rk)

    # Single value walk over the whole (now key-scrubbed) event: rewrites URL /
    # query / DB-statement / free-text values wherever they nest — request data,
    # extra, contexts, breadcrumb data, span data, stack-frame vars, log
    # messages (`message` / `logentry` incl. `params`) and exception `value`s.
    _scrub_value_fields(event, opts)

    # Restore the saved fields with only the email backstop (idempotent, so
    # their already-hashed params aren't hashed a second time).
    if saved_request and isinstance(request_obj, dict):
        for rk, rv in saved_request.items():
            request_obj[rk] = _mask_emails_in_leaves(rv)
    if saved_txn is not None:
        event["transaction"] = _mask_emails_in_leaves(saved_txn)
    for si, desc in saved_span_descs:
        ev_spans = event.get("spans")
        if isinstance(ev_spans, list) and si < len(ev_spans) and isinstance(ev_spans[si], dict):
            ev_spans[si]["description"] = _mask_emails_in_leaves(desc)


_HELD = object()  # inert placeholder the multi-pass phases skip over


def _clean_keys(parent: Dict[Any, Any]) -> bool:
    """True when no key can be rewritten by bytes normalisation (so a held key is stable)."""
    return all(isinstance(k, str) and _strip_bytes_repr_key(k) == k for k in parent)


def _hold_fusable(
    event: Dict[str, Any], content_opts: _PiiOpts
) -> List[Tuple[Any, Any, Any, _PiiOpts]]:
    """Detach the big user-content containers so the fused walk visits them once.

    ``extra`` / ``contexts`` / ``user`` / ``spans[*].data`` / ``breadcrumbs[*].data`` /
    stack-frame ``vars`` would otherwise be walked by the bytes pre-pass, a key-rule
    body pass and the value walk. Each is swapped for :data:`_HELD` (position kept)
    and scrubbed by :func:`_fused_walk` in :func:`_release_held`.
    """
    held: List[Tuple[Any, Any, Any, _PiiOpts]] = []

    def take(parent: Any, key: str, kinds: Tuple[type, ...], bopts: _PiiOpts) -> None:
        if isinstance(parent, dict) and isinstance(parent.get(key), kinds) and _clean_keys(parent):
            held.append((parent, key, parent[key], bopts))
            parent[key] = _HELD

    if _clean_keys(event):
        take(event, "extra", (dict, list), content_opts)
        take(event, "contexts", (dict,), content_opts)
        if isinstance(event.get("user"), dict):
            take(event, "user", (dict,), _user_body_opts(content_opts))
    spans = event.get("spans")
    for span in spans if isinstance(spans, list) else ():
        take(span, "data", (dict, list), content_opts)
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, dict):
        crumbs = crumbs.get("values")
    for crumb in crumbs if isinstance(crumbs, list) else ():
        take(crumb, "data", (dict, list), content_opts)
    for container_key in ("exception", "threads"):
        container = event.get(container_key)
        values = container.get("values") if isinstance(container, dict) else None
        for entry in values if isinstance(values, list) else ():
            stacktrace = entry.get("stacktrace") if isinstance(entry, dict) else None
            frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
            for frame in frames if isinstance(frames, list) else ():
                take(frame, "vars", (dict, list), content_opts)
    return held


def _release_held(held: List[Tuple[Any, Any, Any, _PiiOpts]], opts: _PiiOpts) -> None:
    for parent, key, node, bopts in held:
        parent[key] = _fused_walk(node, bopts, opts)


def scrub_event(
    event: Dict[str, Any],
    hint: Optional[Dict[str, Any]] = None,
    level: PiiLevel = PiiLevel.BASIC,
    hash_salt: str = "",
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
) -> Dict[str, Any]:
    """Apply the per-sink PII level to every event field that can carry PII.

    Beyond request headers, this scrubs the query string (both ``query_string``
    and the query component of ``request.url``), the body (``request.data`` —
    parsed dict/list, or a raw JSON/form string), the ``user`` object (e.g.
    email masking), top-level ``tags``, application ``extra``, custom
    ``contexts``, free-text message fields (``message`` / ``logentry`` incl. its
    interpolation ``params``, and breadcrumb ``message``, with embedded
    emails/URLs masked), exception ``value`` text, breadcrumb ``data`` payloads,
    transaction ``spans[*].data`` (DB statements redacted), the ``transaction``
    name, captured stack-local ``vars`` in exception/thread frames, and the
    CGI/WSGI ``request.env`` (Django ``META``) — ``HTTP_*`` header entries get the
    header rules (``HTTP_AUTHORIZATION`` / ``HTTP_COOKIE`` dropped, etc.) and
    ``QUERY_STRING`` is scrubbed as a query. It honours the operator's
    ``EXTRA_DROP_HEADERS`` / ``EXTRA_MASK_FIELDS`` / ``EXTRA_HASH_FIELDS``
    extensions. ``request.cookies``
    is redacted wholesale. At ``SENSITIVE`` level the client IP is hashed with
    ``hash_salt`` wherever it appears — ``request.env.REMOTE_ADDR`` and forwarded
    ``HTTP_X_FORWARDED_FOR`` / etc., ``user.ip_address``, and proxy-forwarded IP
    headers (``X-Forwarded-For``, ``X-Real-IP``, …). ``NONE`` is a no-op.

    Every access is guarded so a malformed event can never raise inside
    ``before_send`` (which would drop the event).

    The work is split into ordered phases — :func:`_build_opts`,
    :func:`_scrub_request`, :func:`_scrub_metadata`,
    :func:`_scrub_trace_metadata` and :func:`_walk_with_saved_fields` — so each
    concern has a single, testable home.
    """
    if level == PiiLevel.NONE:
        return event

    # Sentry serializes bytes/bytearray leaves to strings *after* before_send, so
    # decode them up front — otherwise a byte-valued leaf bypasses every
    # str-based scrubber and reaches Sentry decoded and raw.
    opts, header_opts, content_opts = _build_opts(
        level, hash_salt, extra_drop, extra_mask, extra_hash
    )
    held = _hold_fusable(event, content_opts)
    _normalize_bytes_leaves(event)

    _scrub_request(event, opts, header_opts, content_opts)
    _scrub_metadata(event, opts, content_opts)
    _scrub_trace_metadata(event, opts, content_opts)
    _walk_with_saved_fields(event, opts)
    _release_held(held, opts)

    if level == PiiLevel.SENSITIVE:
        # An explicit EXTRA_MASK rule for an IP field wins over the IP hash.
        # The semantic "ip" alias targets user.ip_address too (matching the
        # alias expansion in the user object scrubbing above), so expand it
        # here so _hash_ip_fields recognises the mask.
        masked = opts.extra_mask or frozenset()
        if "ip" in masked and "ip_address" not in masked:
            masked = frozenset(masked | {"ip_address"})
        _hash_ip_fields(event, hash_salt, masked)

    return event


def init_sentry(
    dsn: str,
    environment: str,
    traces_sample_rate: float = 0.1,
    before_send: Optional[
        Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Optional[Dict[str, Any]]]
    ] = None,
    pii_level: Optional[PiiLevel] = None,
    include_local_variables: bool = False,
) -> None:
    """Initialize Sentry with Django integration and PII scrubbing.

    Args:
        dsn: Sentry DSN (required, must be valid Sentry DSN URL)
        environment: Environment name (required, max 64 chars, e.g., 'dev', 'prod')
        traces_sample_rate: Sample rate for traces (0.0 to 1.0, default 0.1)
        before_send: Optional ``(event, hint)`` hook for error events. It does not
            replace PII scrubbing: it runs first, on the unscrubbed event, and
            whatever it returns is then scrubbed, so data it adds is covered.
            Returning ``None`` (or a non-dict) drops the event, as does raising.
        pii_level: Optional PII level for Sentry. If None, uses per-sink PII configuration.
        include_local_variables: Send stack-frame local variables with events. Off by
            default: they hold arbitrary application data, and key-based scrubbing
            cannot recognise all of it (a name in a variable called ``full_name``).
            When on, the usual PII scrubbing still applies, which does not redact
            every sensitive value (e.g. names, or credentials nested in a local).

    Unless the Sentry level is ``NONE``, breadcrumbs and events created by the SDK
    from log records first get the log record sanitization, at the stricter of the
    ``logs`` and Sentry levels (see :mod:`observe_kit.sentry.logs`). ``before_send``
    sees that data already sanitized; ``hint["log_record"]`` is the original record.

    Raises:
        ConfigurationError: If any configuration parameter is invalid
    """
    # Validate configuration
    _validate_dsn(dsn)
    _validate_environment(environment)
    _validate_traces_sample_rate(traces_sample_rate)

    # Use per-sink PII config if pii_level not explicitly provided
    if pii_level is None:
        pii_config = get_pii_config()
        pii_level = pii_config.get_level(PII_SINK_SENTRY)

    # Resolve the hash salt (for SENSITIVE IP hashing) and the operator's
    # EXTRA_* PII field-set extensions, so Sentry scrubbing honours the same
    # custom keys as the other sinks. Only guard the import — a genuine failure
    # inside get_observe_kit_settings() should surface, not be swallowed.
    hash_salt = ""
    extra_drop: Optional[FrozenSet[str]] = None
    extra_mask: Optional[FrozenSet[str]] = None
    extra_hash: Optional[FrozenSet[str]] = None
    try:
        from ..settings import get_observe_kit_settings
    except ImportError:  # pragma: no cover - Django not installed
        logger.debug("observe_kit: settings unavailable; Sentry scrubbing uses built-in PII sets")
    else:
        cfg = get_observe_kit_settings()
        hash_salt = cfg.pii_hash_salt or ""
        extra_drop = cfg.extra_drop_headers
        extra_mask = cfg.extra_mask_fields
        extra_hash = cfg.extra_hash_fields

    if pii_level == PiiLevel.SENSITIVE and not hash_salt:
        logger.warning(
            "observe_kit: PII_HASH_SALT is empty; Sentry SENSITIVE IP hashing will be unsalted "
            "and reversible via rainbow tables. Set OBSERVE_KIT['PII_HASH_SALT']."
        )

    def _scrubber(event: Dict[str, Any], hint: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return scrub_event(event, hint, pii_level, hash_salt, extra_drop, extra_mask, extra_hash)

    def _before_send(
        event: Dict[str, Any], hint: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        # No try/except: sentry-sdk drops the event when before_send raises.
        if before_send is not None:
            result = before_send(event, hint)
            if not isinstance(result, dict):
                return None
            # The scrubber mutates nested containers in place, and the hook may have
            # attached app-owned ones.
            event = copy.deepcopy(result)
        return _scrubber(event, hint)

    integrations: List[Integration] = [DjangoIntegration()]
    before_breadcrumb = None
    if pii_level != PiiLevel.NONE:
        from .logs import LogRecordIntegration  # imports this module

        log_records = LogRecordIntegration(pii_level)
        integrations.append(log_records)
        before_breadcrumb = log_records.before_breadcrumb

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=integrations,
        before_breadcrumb=before_breadcrumb,
        traces_sample_rate=traces_sample_rate,
        include_local_variables=include_local_variables,
        before_send=_before_send,  # type: ignore[arg-type]
        # Transaction (trace) events go through a separate hook; scrub them too,
        # otherwise transaction names / span data would bypass PII_LEVELS["sentry"].
        before_send_transaction=_scrubber,  # type: ignore[arg-type]
    )
    logger.info("sentry configured", extra={"environment": environment, "pii_level": pii_level})
