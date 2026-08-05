from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Set,
    Tuple,
    TypeGuard,
    cast,
)
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlsplit, urlunsplit

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from ..conf import PII_SINK_SENTRY, cgi_header_name
from ..pii_rules import (
    _EMAIL_RE,
    PiiLevel,
    _hash_value,
    effective_sets,
    get_pii_config,
    sanitize_body,
    sanitize_mapping,
    sanitize_query_params,
)
from ..pii_rules import _is_pair_list as _pii_is_pair_list
from .scrub.constants import REDACTED
from .scrub.decode import MAX_DECODE_PASSES as _MAX_NESTED_URL_DECODE_PASSES
from .scrub.decode import bounded_unquote as _bounded_unquote
from .scrub.emails import _dedupe_masked_key, _mask_email_key, _mask_emails, _mask_emails_in_leaves

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

# Matches an absolute or scheme-relative URL token embedded in free text (e.g. a
# span description). Case-insensitive, and intentionally scheme-generic so DSNs
# like ``postgres://user:secret@host/db`` are routed through ``_scrub_url``
# and have authority credentials redacted too. The body stops at prose separators
# (``, ; | ) ] }`` and quote/angle delimiters ``" ' < >``) — the same set
# ``_REL_URL_RE`` / ``_URL_TOKEN_SEPARATOR_RE`` use — so comma-delimited prose
# after a visible URL (``…?phone=…,progress=100%25``) isn't swallowed into the
# URL and masked away. A comma, semicolon or closing paren *inside* a query is
# still valid URI syntax (``, ; ) `` are RFC 3986 sub-delims), so a URL token is
# allowed to continue past one when it is followed by more query structure joined
# with ``&`` (``?x=a,b&phone=…``, ``?x=f(a)&phone=…``): without that, a sensitive
# parameter after the delimiter would be left raw in the message.
_URL_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^\s,;|)\]}<>\"']+"
    r"(?:[,;)][^\s&,;|)\]}<>\"']*&[^\s,;|)\]}<>\"']*=[^\s,;|)\]}<>\"']*)*",
    re.IGNORECASE,
)

# Matches a *relative* URL token carrying a ``key=value`` query *or* fragment in
# free text — a path starting with ``/`` followed by ``?key=value``
# (``GET /search?phone=…``) or a query-like ``#key=value`` fragment
# (``/callback#access_token=…``, an OAuth implicit-flow redirect). Absolute URLs
# are handled by ``_URL_RE`` above; the lookbehind keeps this from re-matching the
# path inside one (``…test/p?x=1`` — ``/p`` follows a word char) or the ``:/`` of
# a scheme. A ``key=value`` after the delimiter is required so ordinary prose like
# ``and/or?maybe`` / ``see #section`` isn't rewritten. Stops at quote/angle
# delimiters too, and continues past a comma or semicolon followed by
# ``&``-joined query structure (matching ``_URL_RE``).
_REL_URL_RE = re.compile(
    r"(?<![\w:/])/[^\s?#\"'<>]*[?#]"
    r"[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*"
    r"(?:[,;][^\s&,;|)\]}<>\"']+&[^\s#,;|)\]}<>\"']*=[^\s#,;|)\]}<>\"']*)*"
)

# Matches a *rootless* relative URL token in free text — a word-path with no
# leading ``/`` that still carries a query or fragment (``callback?phone=…``,
# ``callback#access_token=…``, and multi-segment ``account/callback?phone=…``).
# ``_REL_URL_RE`` requires a ``/`` start, so a
# valid rootless reference like ``redirect callback?phone=0812345678`` was left
# raw and its query/fragment PII exposed. The first path char deliberately
# excludes ``/`` so a leading-slash URL (already handled by ``_REL_URL_RE``)
# isn't double-scrubbed, while later segments may contain ``/``. Require a
# ``key=value`` after the delimiter so ordinary prose (``see it? maybe``,
# ``why #section``) isn't rewritten, and keep the lookbehind so the word-path
# isn't matched mid-token.
_ROOTLESS_URL_RE = re.compile(r"(?<![\w/])[\w@.+~-][\w@.+/~-]*[?#][\w.%\-]+=[^\s,;|)\]}<>\"']*")

# Splits URL tokens that are adjacent through common prose separators, e.g.
# ``https://safe.test,postgres://user:secret@host/db``. Keep separators out of
# each URL parse so every authority gets scrubbed independently.
_URL_TOKEN_SEPARATOR_RE = re.compile(r"([,;|)\]}])(?=(?:[A-Za-z][A-Za-z0-9+.-]*:)?//)")
_URL_TOKEN_NEXT_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|//(?=[^/?#\s]*@)")

# A URL whose query holds another URL (``next=/p?next=/p?…``) re-enters the URL
# scrubber once per level. An attacker can nest hundreds of levels to blow the
# Python stack inside ``before_send`` (dropping the event) and balloon the
# ``urlencode`` output, so a recursion budget is threaded through the URL-scrub
# cycle and the value is redacted once it is exhausted. Legitimate nested
# redirects are only a level or two deep.
_MAX_URL_NESTING = 20

# Match a percent-encoded URL query (``%3F`` → ``?``) or fragment (``%23`` → ``#``)
# delimiter at any nesting depth (``%253F``/``%2523`` …). Used to expose *only*
# the structural delimiters an attacker hid via encoding, leaving unrelated path
# escapes (``%2F``/``%20``) intact when reconstructing a scrubbed URL.
_URL_QUERY_DELIM_RE = re.compile(r"%(?:25)*3[Ff]")
_URL_FRAG_DELIM_RE = re.compile(r"%(?:25)*23")

# Matches a *percent-encoded URL substring* hidden inside free text: it starts at
# an encoded ``/`` (``%2F``), ``:`` (``%3A``), ``?`` (``%3F``) or ``#`` (``%23``) —
# the structural delimiters that mark URL syntax — then runs through URL-ish
# characters and percent escapes, stopping at whitespace or a prose separator
# (``, ; | ) ] }``). Anchoring on an encoded delimiter lets the surrounding text
# (a ``redirect:`` prefix, an adjacent ``hello%20world`` / ``progress=100%25``) be
# preserved so only the hidden URL slice is scrubbed. Ordinary encoded text like
# ``%20`` / ``%25`` never matches the anchor, so a decoded-form
# ``_value_carries_url`` gate still guards against false positives.
_HIDDEN_ENCODED_URL_RE = re.compile(
    r"%(?:25)*(?:2[Ff]|3[AaFf]|23)"
    r"(?:[\w.~!$'*+@:/?#=&\-]|%[0-9A-Fa-f]{2})*"
)

# ``_EMAIL_RE`` (embedded, free-text) and ``_ENCODED_EMAIL_RE`` (percent-encoded)
# are defined in ``pii_rules`` alongside ``_BARE_EMAIL_RE`` so email grammar has a
# single owning module; they are imported at the top of this file.

# Well-known keys whose *value* is a full or relative URL / a bare path. These
# are the OTel/Sentry semantic span-data attribute names (plus the plain ``url``
# key), lower-cased for matching. ``http.target`` is a path+query while
# ``url.path`` and the ``http.path`` tag are bare paths (this repo's OTel
# middleware / SentryContextMiddleware set them from ``request.get_full_path()``
# / ``request.path``); ``urlsplit`` parses the relative forms fine, so
# ``_scrub_url`` scrubs their query / path emails (including encoded ``%40``).
_URL_VALUE_KEYS = frozenset({"url", "http.url", "url.full", "http.target", "url.path", "http.path"})
_QUERY_VALUE_KEYS = frozenset({"http.query", "url.query"})

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
    the original was more than a bare email (fails :data:`_EMAIL_RE` ``fullmatch``),
    redact the whole value. A SHA256 hash or a clean whole-value email mask
    (``a***@example.com``) is kept as-is.
    """
    if _is_sha256_hex(masked):
        return masked
    if isinstance(original, str) and "@" in original and not _EMAIL_RE.fullmatch(original):
        return _REDACTED
    return masked


def _url_form_to_scrub(value: str) -> Optional[str]:
    """Choose which form of a URL value to hand to :func:`_scrub_url`.

    When decoding exposes a new query, fragment, or ``user:pass@`` authority that
    ``urlsplit`` would otherwise miss (``%3F``/``%23``/``%40`` hiding structure),
    reconstruct a URL that exposes *only* those structural delimiters and carries
    the fully decoded query/fragment (which the scrubber needs decoded to parse
    and mask), while keeping the original path's unrelated escapes (``%2F``/
    ``%20``) intact rather than rewriting them. When decoding reveals no hidden
    structure, return the original ``value`` unchanged. Returns ``None`` when the
    decode cap is exhausted, signalling the caller to redact the value wholesale.
    """
    decoded, exhausted = _bounded_unquote(value)
    if exhausted:
        return None
    if decoded == value:
        return value
    try:
        orig_parts = urlsplit(value)
        dec_parts = urlsplit(decoded)
    except ValueError:
        return value
    # An encoded query/fragment delimiter hidden in the *path* is structure even
    # when the URL already carries a visible outer query — e.g.
    # ``…/search%3Fphone%3D…?x=1`` must still be scrubbed, so testing
    # ``dec_parts.query and not orig_parts.query`` would wrongly skip it. Detect
    # the encoded delimiter in the path directly.
    authority_revealed = "@" in dec_parts.netloc and "@" not in orig_parts.netloc
    # A query/fragment delimiter can hide in the *authority* too: when the path
    # separator ``/`` is itself encoded (``https://host!%252Fsearch%253Fphone%253D…``),
    # ``urlsplit`` sees the whole ``host!%2Fsearch%3Fphone%3D…`` as the netloc, so
    # the path-based detection below misses it and ``_scrub_url`` can't reach the
    # buried query. ``_URL_RE`` happily matches such a token (``! ( : =`` aren't
    # host-invalid to it), so without this the encoded query would be exempted
    # from the hidden-URL pass yet never scrubbed — a leak.
    netloc_hides_structure = bool(_URL_QUERY_DELIM_RE.search(orig_parts.netloc)) or bool(
        _URL_FRAG_DELIM_RE.search(orig_parts.netloc)
    )
    reveals_structure = (
        bool(_URL_QUERY_DELIM_RE.search(orig_parts.path))
        or bool(_URL_FRAG_DELIM_RE.search(orig_parts.path))
        or authority_revealed
        or netloc_hides_structure
    )
    if not reveals_structure:
        return value  # only ordinary escapes decoded — keep the original form
    if netloc_hides_structure and not authority_revealed:
        # The path boundary was encoded, so the query/fragment is buried in what
        # urlsplit parsed as the authority. Hand ``_scrub_url`` the decoded form
        # to reparse and scrub the exposed query/fragment.
        return decoded
    if authority_revealed:
        # The authority was opaque-encoded. When the *whole* structure was encoded
        # (a fully double-encoded ``https%253A%252F%252Falice%253Asecret%2540host``),
        # ``urlsplit(value)`` sees no scheme/authority and the encoded-path branch
        # below would return the value unscrubbed — the email fallback would then
        # decode it and expose the ``user:pass@`` credentials. Hand ``_scrub_url``
        # the decoded form so it redacts the authority. When the scheme is already
        # visible (``https://alice%3Asecret%40host/a%2Fb``), only the authority was
        # encoded: rebuild with the decoded authority while keeping the original
        # path/query/fragment escapes, so ``a%2Fb`` stays a single path segment
        # instead of being flattened to ``a/b``.
        if orig_parts.scheme:
            return urlunsplit(
                (
                    orig_parts.scheme,
                    dec_parts.netloc,
                    orig_parts.path,
                    orig_parts.query,
                    orig_parts.fragment,
                )
            )
        return decoded
    # Expose the encoded query/fragment delimiters *within the path only* so the
    # path keeps its unrelated escapes and a pre-existing outer query/fragment is
    # preserved rather than merged into the newly exposed one. The authority is
    # left encoded — ``_scrub_url`` decodes ``%40`` userinfo itself.
    exposed_path = _URL_FRAG_DELIM_RE.sub("#", _URL_QUERY_DELIM_RE.sub("?", orig_parts.path))
    path_parts = urlsplit(exposed_path)  # splits the path into path/query/fragment
    # Decode the hidden query by exactly the outer query delimiter's encoding depth
    # (``%3F`` → one level, ``%253F`` → two, …) so ``parse_qsl`` can split
    # ``key=value`` pairs and ``&`` separators, while a more deeply encoded octet
    # inside a value (``%2526`` beside a single-encoded ``%3F``) stays encoded.
    hidden_query = path_parts.query
    if hidden_query:
        qmatch = _URL_QUERY_DELIM_RE.search(orig_parts.path)
        levels = (len(qmatch.group()) - 3) // 2 + 1 if qmatch else 1
        for _ in range(levels):
            hidden_query = unquote(hidden_query)
    # Merge the exposed path-carried query with any already-parsed outer query.
    if hidden_query and orig_parts.query:
        query = f"{hidden_query}&{orig_parts.query}"
    else:
        query = hidden_query or orig_parts.query
    fragment = path_parts.fragment or orig_parts.fragment
    return urlunsplit((orig_parts.scheme, orig_parts.netloc, path_parts.path, query, fragment))


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
        or _URL_RE.search(value) is not None  # embedded scheme-relative URL in free text
        or "://" in value  # any URI scheme: postgres://, redis://, ftp://, …
        # A relative (``/search?phone=…``) or rootless (``callback?phone=…``)
        # URL token carrying a query/fragment. These require the ``?key=value`` to
        # be *contiguous* with a path token, so conversational prose with a stray
        # ``?``/``#`` and a later ``=`` (``Are you sure? answer=yes``,
        # ``prefix# section=one``) is not misclassified as a URL and mangled by
        # the structural parser — such a leaf is left to the email backstop.
        or _REL_URL_RE.search(value) is not None
        or _ROOTLESS_URL_RE.search(value) is not None
        # A *bare* query/fragment token that leads the value (``#access_token=…``
        # exposed from an encoded ``%23``, ``?key=value``) — no path prefix for the
        # regexes above to anchor on. Require no whitespace so it stays a URL-ish
        # token and prose (which carries its ``?``/``#`` mid-sentence, or contains
        # spaces) is still excluded.
        or (value[:1] in "?#" and "=" in value and not any(c.isspace() for c in value))
    )


def _scrub_url_token(token: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Scrub one URL regex token, splitting adjacent URLs before each URL start."""
    parts = _URL_TOKEN_SEPARATOR_RE.split(token)
    scrubbed: List[str] = []
    for part in parts:
        if not part or _URL_TOKEN_SEPARATOR_RE.fullmatch(part):
            scrubbed.append(part)
            continue

        start = 0
        for match in _URL_TOKEN_NEXT_URL_RE.finditer(part):
            if match.start() == 0:
                continue
            scrubbed.append(_scrub_url(part[start : match.start()], opts, depth))
            start = match.start()
        scrubbed.append(_scrub_url(part[start:], opts, depth))
    return "".join(scrubbed)


def _scrub_urlish_value(value: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Scrub a URL-like value, treating mixed prose as free text."""
    matches = [m for m in (_URL_RE.search(value), _REL_URL_RE.search(value)) if m is not None]
    if not matches:
        return _scrub_url(value, opts, depth)
    first = min(matches, key=lambda m: m.start())
    if first.start() == 0 and first.end() == len(value):
        if "%" in value:
            decoded = value
            for _ in range(_MAX_NESTED_URL_DECODE_PASSES):
                next_decoded = unquote(decoded)
                if next_decoded == decoded:
                    break
                decoded = next_decoded
                for m in (_URL_RE.finditer(decoded), _REL_URL_RE.finditer(decoded)):
                    for submatch in m:
                        # Any interior match (something precedes it) means the
                        # token is mixed content — route it through the free-text
                        # scrubber. An end-aligned match (``submatch.end() ==
                        # len(decoded)``) counts too: a visible URL followed by an
                        # encoded one leaks if the encoded suffix is treated as a
                        # plain path by the visible-URL parser.
                        if submatch.start() > 0:
                            return _scrub_text(value, opts, depth)
        return _scrub_url_token(value, opts, depth)
    return _scrub_text(value, opts, depth)


def _scrub_url_if_carries(value: str, opts: _PiiOpts, depth: int = 0) -> Optional[str]:
    """Return the scrubbed value if it is / carries a URL, else ``None``.

    ``parse_qsl`` decodes a query value only once, so a multiply encoded redirect
    can still arrive here without literal URL structure. Decode with a small
    fixed limit until the value stabilizes or exposes a URL, then scrub the
    decoded form. ``None`` lets the caller fall back to :func:`_mask_emails`.

    A value that carries a URL in free text (``"connect https://user:secret@host/db"``)
    is sent through :func:`_scrub_text`, which uses ``_URL_RE`` to find individual
    URL tokens so ``urlsplit`` receives a clean URL and can strip ``user:pass@``
    credentials from the netloc.
    """
    if _value_carries_url(value):
        return _scrub_urlish_value(value, opts, depth)
    if "%" in value:
        decoded = value
        for _ in range(_MAX_NESTED_URL_DECODE_PASSES):
            next_decoded = unquote(decoded)
            if next_decoded == decoded:
                break
            decoded = next_decoded
            if _value_carries_url(decoded):
                return _scrub_urlish_value(decoded, opts, depth)
        else:
            if unquote(decoded) != decoded:
                return _REDACTED
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
    ``urlencode`` would otherwise re-emit the raw key.
    """
    scrubbed: List[Tuple[str, str]] = []
    for key, value in parse_qsl(query_string, keep_blank_values=True):
        for k, v in opts.mapping({key: value}).items():
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
            scrubbed.append((cast(str, _mask_emails(k)), v))
    return urlencode(scrubbed)


def _scrub_url(url: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Expose any hidden encoded URL structure, then scrub the URL.

    Exposing an attacker-hidden encoded query/fragment/authority (``%3F``/``%23``/
    ``%40``) is an invariant of URL scrubbing, so it happens here first — that way
    *every* caller (free text, generic leaves, URL fields, URL headers) is covered
    uniformly and no path can hand ``urlsplit`` a still-encoded URL and mistake a
    hidden query for path text (see :func:`_url_form_to_scrub`).

    The query component is then masked per the field rules plus email-value masking;
    unambiguous email tokens embedded in the path (``/users/alice@example.com``,
    literal or percent-encoded ``%40``) are masked; a query-like or email-bearing
    fragment (``#access_token=…`` / ``#email=…``, possibly percent-encoded) is
    redacted wholesale while a plain anchor (``#section``) is kept. Userinfo in
    the authority (``https://user:secret@host/…``) is redacted wholesale — it can
    carry basic-auth credentials that neither the query nor field rules catch.

    ``urlsplit`` raises ``ValueError`` on malformed URLs (e.g. ``http://[``).
    Since this runs inside ``before_send`` — which must never raise or the whole
    event is dropped — fall back to redacting the query wholesale so no raw
    query PII can leak from a URL we can't parse.

    ``depth`` bounds the URL→query→nested-URL recursion: a query value that is
    itself a URL re-enters here one level deeper, so a maliciously nested
    ``next=/p?next=/p?…`` is redacted once :data:`_MAX_URL_NESTING` is exceeded
    rather than overflowing the stack inside ``before_send``.
    """
    if depth > _MAX_URL_NESTING:
        return _REDACTED
    form = _url_form_to_scrub(url)
    if form is None:  # decode cap exhausted while still changing → don't leak
        return _REDACTED
    url = form
    try:
        parts = urlsplit(url)
    except ValueError:
        # Can't parse to isolate the components. If the authority (after the
        # scheme, or a protocol-relative ``//``, up to the next ``/ ? #``)
        # carries ``user:pass@`` credentials we can't strip them precisely, so
        # redact the whole URL rather than leak them; otherwise redact the query
        # and any query-like/email fragment (matching the parsed-path behaviour)
        # while keeping a plain anchor.
        after_scheme = url.split("://", 1)[-1]
        if after_scheme.startswith("//"):  # scheme-relative //authority form
            after_scheme = after_scheme[2:]
        authority = re.split(r"[/?#]", after_scheme, maxsplit=1)[0]
        if "%" in authority:  # decode ``%40`` etc. only when something is encoded
            authority = _bounded_unquote(authority)[0]
        if "@" in authority:
            return _REDACTED
        head, hash_sep, frag = url.partition("#")
        base, query_sep, _ = head.partition("?")
        # Mask path emails in the surviving base too (literal or %40-encoded),
        # so a malformed URL like ``http://[/users/alice@example.com?x=1`` can't
        # leak the path email that the parsed branch below would have masked.
        # Apply to the encoded form to preserve unrelated percent-encoding.
        base = _mask_emails(base)
        result = f"{base}?{_REDACTED}" if query_sep else base
        if hash_sep:
            result = f"{result}#{_REDACTED}" if _fragment_carries_pii(frag) else f"{result}#{frag}"
        return result
    netloc = parts.netloc
    if "@" in netloc:  # strip user:password@, keep host[:port] as written
        netloc = f"{_REDACTED}@{netloc.rsplit('@', 1)[1]}"
    elif "%" in netloc:  # encoded ``%40`` userinfo — decode only when encoded
        decoded_netloc, exhausted = _bounded_unquote(netloc)
        if exhausted:
            netloc = _REDACTED
        elif "@" in decoded_netloc:
            netloc = f"{_REDACTED}@{decoded_netloc.rsplit('@', 1)[1]}"
    # Mask emails in the path, whether the ``@`` is literal or percent-encoded
    # (``/users/alice%40example.com``). Apply ``_mask_emails`` to the *encoded*
    # path so unrelated percent-encoding like ``%2F`` isn't decoded as a side
    # effect — a path like ``/users/alice%40example.com/files/a%2Fb`` keeps
    # its ``a%2Fb`` segment intact. ``redact_on_exhaust=False`` masks a deeply
    # encoded path email in place (via :func:`_mask_emails_basic`) instead of
    # redacting the whole path, redacting only when even that finds nothing.
    path = _mask_emails(parts.path, redact_on_exhaust=False)
    query = _scrub_query_string(parts.query, opts, depth + 1) if parts.query else parts.query
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
    if _is_pair_list(data):
        return _scrub_pair_list(data, content_opts)
    if isinstance(data, (dict, list)):
        return content_opts.body(data)
    if isinstance(data, tuple):
        return content_opts.body(list(data))
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
    if _strip_bytes_repr_key(key_lower) != key_lower:
        # A colliding bytes-repr key the subtree body pass couldn't match: apply
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
        for key, value in list(obj.items()):
            masked_key = _dedupe_masked_key(rebuilt, _mask_email_key(key))
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
    value = _content_opts(opts).body(value)
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
        sanitized = opts.mapping({key_lower: value})
        if key_lower not in sanitized:  # operator drop
            return _REDACTED
        if sanitized[key_lower] != value:  # operator mask / hash
            return _scrub_masked_field_result(key_lower, value, sanitized[key_lower], opts)
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
    if rule is not None:
        # Non-string value under a walk-managed key — the body pass deliberately
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
        if isinstance(crumb, dict) and isinstance(crumb.get("data"), (dict, list)):
            crumb["data"] = opts.body(crumb["data"])


def _scrub_hidden_encoded_urls(text: str, opts: _PiiOpts, depth: int = 0) -> str:
    """Scrub percent-encoded URLs / nested queries hidden in free text.

    A *visible* URL in a leaf must not shadow an *encoded* redirect elsewhere in
    the same text (``https://safe.test then %252Fsearch%253Fphone%253D…``) — the
    visible-URL path sends mixed prose here via :func:`_scrub_text`, and the plain
    ``_URL_RE`` / ``_REL_URL_RE`` passes only see already-decoded structure, so the
    encoded portion would reach Sentry unchanged.

    Only the encoded-URL *substring* (``_HIDDEN_ENCODED_URL_RE``, anchored on an
    encoded ``/``/``:``/``?``) is bounded-decoded and scrubbed, so the surrounding
    text is preserved verbatim — a ``redirect:`` prefix, a trailing ``,hello%20world``,
    or an adjacent ``progress=100%25`` keep their original encoding. Each candidate
    is scrubbed only when its fully decoded form actually carries a URL; if the
    structural detector sees a URL the scrubber can't isolate, the candidate is
    redacted rather than restored (never leak a recoverable encoded secret).
    """
    if "%" not in text:
        return text

    # Spans of every *visible* URL ``_URL_RE`` recognizes in this text (both
    # ``scheme://…`` and scheme-relative ``//…`` forms). An encoded slice that
    # falls inside one of these belongs to the ``_URL_RE`` pass, which scrubs the
    # whole URL as a unit; anything outside is a genuinely hidden slice we own.
    url_spans = [match.span() for match in _URL_RE.finditer(text)]

    def _repl(match: "re.Match[str]") -> str:
        # If this encoded slice sits inside a *visible* URL token, leave it for the
        # ``_URL_RE`` pass, which scrubs the whole URL as a unit — redacting a
        # deeply-encoded authority (``https://user%253A…%2540host``) wholesale
        # instead of letting this pass fragment it into a surviving
        # ``https://<username>`` prefix, and avoiding re-scrubbing a value that
        # ``_scrub_url`` has already scrubbed and re-encoded (which would e.g. hash
        # a nested ``EXTRA_HASH_FIELDS`` token twice). Bound the exemption to the
        # actual ``_URL_RE`` match span rather than any earlier ``://`` in the
        # whitespace token: an encoded slice that follows a visible URL after prose
        # punctuation (``https://safe.test,%252Fsearch%253Fphone%253D…``) sits
        # *outside* the URL — ``_URL_RE`` stops at the comma — so it must be scrubbed
        # here, while a scheme-relative URL (``//h/p?next=%2Fa%3Ftoken%3D…``) is
        # correctly recognized and left for the single ``_URL_RE`` scrub.
        start = match.start()
        if any(url_start <= start < url_end for url_start, url_end in url_spans):
            return match.group(0)
        candidate = match.group(0)
        decoded = candidate
        for _ in range(_MAX_NESTED_URL_DECODE_PASSES):
            next_decoded = unquote(decoded)
            if next_decoded == decoded:
                break
            decoded = next_decoded
        if decoded == candidate:
            return candidate
        if unquote(decoded) != decoded:
            return _REDACTED
        if _value_carries_url(decoded):
            scrubbed = _scrub_urlish_value(decoded, opts, depth)
            # ``scrubbed == decoded`` means the URL was isolated but contained
            # no values requiring scrubbing — keep the original candidate rather
            # than redacting it, so benign encoded URLs (``%2Fdocs%3Fpage%3D1``)
            # aren't turned into ``[Filtered]`` while their visible equivalent
            # is preserved.
            return scrubbed if scrubbed != decoded else candidate
        return candidate

    return _HIDDEN_ENCODED_URL_RE.sub(_repl, text)


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
    text = _scrub_hidden_encoded_urls(text, opts, depth)
    text = _URL_RE.sub(lambda m: _scrub_url_token(m.group(0), opts, depth), text)
    text = _REL_URL_RE.sub(lambda m: _scrub_url(m.group(0), opts, depth), text)
    text = _ROOTLESS_URL_RE.sub(lambda m: _scrub_url(m.group(0), opts, depth), text)
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


def _scrub_spans(event: Dict[str, Any], opts: _PiiOpts) -> None:
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
            span["description"] = _scrub_span_description(description, span.get("op"), opts)


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
            stacktrace = entry.get("stacktrace") if isinstance(entry, dict) else None
            frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
            if not isinstance(frames, list):
                continue
            for frame in frames:
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


def _scrub_single_param(key: str, value: Any, opts: _PiiOpts) -> Optional[Any]:
    """Apply field rules to a single query param value, returning ``None`` if dropped.

    Handles both the key-level rules (drop/mask/hash) and the URL-value backstop
    (a value that is itself a URL or carries a nested query gets its inner params
    scrubbed). Used by the dict and list-of-pairs ``query_string`` branches to
    avoid calling ``sanitize_mapping`` on a list-valued dict entry (which would
    stringify the list and mask the repr, leaking individual elements).
    """
    sanitized = opts.mapping({key: value})
    if key not in sanitized:
        return None
    sv = sanitized[key]
    if sv != value:  # a mask/hash rule matched — redact a partial email mask
        return _redact_partial_email_mask(value, sv)
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


def _scrub_request(
    event: Dict[str, Any], opts: _PiiOpts, header_opts: _PiiOpts, content_opts: _PiiOpts
) -> None:
    """Scrub the ``request`` object — headers, cookies, fragment, query, body, env."""
    request = event.get("request")
    if not isinstance(request, dict):
        return
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = header_opts.mapping(headers)
    elif isinstance(headers, (list, tuple)):
        request["headers"] = _scrub_pair_list(list(headers), header_opts)
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
            # Apply the field rules to each entry. A value that is a list
            # (repeated params like ``?token=a&token=b``) has each element
            # scrubbed individually via ``_scrub_single_param`` instead of
            # passing the whole list through ``sanitize_mapping`` (which
            # stringifies it and masks the repr, leaking individual elements
            # that survive ``_mask_value``).
            cleaned: Dict[str, Any] = {}
            for qk, orig in query_string.items():
                if isinstance(orig, (list, tuple)):
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
                    k, v = pair
                    if isinstance(v, (list, tuple)):
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


def _scrub_metadata(event: Dict[str, Any], opts: _PiiOpts, content_opts: _PiiOpts) -> None:
    """Scrub top-level ``tags`` / ``extra`` / ``contexts`` / ``user``."""
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
            user_opts = replace(
                user_opts, extra_hash=frozenset(user_opts.extra_hash - {"ip_address"})
            )
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
            # embedded emails/URLs (and their query PII) via _URL_RE.
            event["transaction"] = _scrub_text(transaction, opts)
    _scrub_spans(event, content_opts)
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
    _normalize_bytes_leaves(event)

    opts, header_opts, content_opts = _build_opts(
        level, hash_salt, extra_drop, extra_mask, extra_hash
    )

    _scrub_request(event, opts, header_opts, content_opts)
    _scrub_metadata(event, opts, content_opts)
    _scrub_trace_metadata(event, opts, content_opts)
    _walk_with_saved_fields(event, opts)

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
        Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Dict[str, Any]]
    ] = None,
    pii_level: Optional[PiiLevel] = None,
) -> None:
    """Initialize Sentry with Django integration and PII scrubbing.

    Args:
        dsn: Sentry DSN (required, must be valid Sentry DSN URL)
        environment: Environment name (required, max 64 chars, e.g., 'dev', 'prod')
        traces_sample_rate: Sample rate for traces (0.0 to 1.0, default 0.1)
        before_send: Optional custom before_send callback
        pii_level: Optional PII level for Sentry. If None, uses per-sink PII configuration.

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

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[DjangoIntegration()],
        traces_sample_rate=traces_sample_rate,
        before_send=before_send or _scrubber,  # type: ignore[arg-type]
        # Transaction (trace) events go through a separate hook; scrub them too,
        # otherwise transaction names / span data would bypass PII_LEVELS["sentry"].
        before_send_transaction=_scrubber,  # type: ignore[arg-type]
    )
    logger.info("sentry configured", extra={"environment": environment, "pii_level": pii_level})
