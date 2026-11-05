from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, MutableMapping, Optional, Set

from .conf import DEFAULT_PII_LEVELS, DROP_HEADERS, HASH_FIELDS, MASK_FIELDS

# A *bare* email address, anchored at both ends. ``_mask_value`` only keeps the
# domain visible when the whole value is a lone email — otherwise the text after
# the ``@`` is not a plain domain and masking just the local part would leak it
# (``alice@ex.com recovery=secret`` → ``a***@ex.com recovery=secret``), so the
# value is redacted wholesale instead. The domain is a dotted name of LDH labels
# — the TLD accepts letters *and* digits/hyphens so punycode/IDN TLDs
# (``xn--11b4c3d``) and Unicode labels (``例え.テスト``) match — or an RFC 5321
# domain literal (``[192.0.2.1]`` / ``[IPv6:…]``). The ``\Z`` anchor is what
# actually distinguishes a lone email from ``email + trailing secret``.
_BARE_EMAIL_RE = re.compile(
    r'(?:"(?:[^"\\]|\\.)+"|[^\s@]+)@(?:[\w\-]+(?:\.[\w\-]+)*\.[\w\-]{2,}|\[[^\[\]\s]+\])\Z'
)

# Matches an email address embedded in free-text message fields. Supports
# ASCII and internationalized (Unicode) email addresses — the local part
# and domain accept ``\w`` (Unicode letters, digits, underscore) plus the
# special characters valid in email syntax. The domain is either a normal
# dotted name (``example.com``), a *single-label* name (``localhost`` /
# ``mailserver`` — valid per RFC 5321 and common in dev/test data, so the
# whole-event backstop must mask it or ``alice@localhost`` leaks), or an
# RFC 5321 *domain literal* — a bracketed
# IPv4/IPv6 address such as ``alice@[192.0.2.1]`` or
# ``alice@[IPv6:2001:db8::1]`` — which carries no dot/TLD and so would escape
# the dotted-domain branch, leaving the (PII) local part unmasked.
# The unquoted local part is an RFC 5322 dot-atom. Besides ``\w``/``.`` it may
# carry atext specials — masking only the ``[\w.%+-]`` suffix would leave a prefix
# like ``alice!`` / ``o'`` visible. Add the specials that never double as URL
# delimiters or appear in mask output: ``! $ ' ^ ` { | } ~``. The URL-structural
# atext chars ``/ ? # & = *`` are deliberately excluded — the email backstop runs
# over text that still contains URLs and already-masked spans (``_scrub_url`` masks
# ``parts.path``; ``_scrub_text`` re-runs this after URL scrubbing), so allowing
# them would let a match swallow ``/users/…`` path segments or a ``?email=…`` query
# and corrupt the URL. Those characters are extremely rare in real local parts.
# NOTE: kept distinct from ``_BARE_EMAIL_RE`` on purpose — this one is *unanchored*
# (embedded matching in prose) and accepts single-label domains, whereas the bare
# matcher is whole-value anchored for ``_mask_value``. Both live here so email
# grammar has a single owning module.
_EMAIL_RE = re.compile(
    r"(?:\"(?:[^\"\\]|\\.)+\"|[\w.%!$'^`{|}~+\-]+)"
    r"@(?:[\w.\-]+\.[^\W\d_]{2,}|\[[^\[\]\s]+\]|[^\W\d_]{2,})"
)

# Matches a percent-encoded email (``@`` → ``%40``) in a value that wasn't
# routed through a URL/query parser (a free-text message, a generic ``extra``
# leaf, …). Both the local part *and* the domain may carry percent-encoded
# octets — a plus-addressed ``alice%2Btag``, an encoded local dot
# ``alice%2Etest``, an encoded domain dot ``example%2Ecom``, or even encoded TLD
# letters ``example%2E%63%6f%6d`` — so the local part, the domain labels *and*
# the TLD accept any ``%XX`` *except* the ``%40`` separator (negative lookahead),
# and the label separator may be a literal ``.`` or ``%2E``. Were ``%`` excluded
# outright, the match would start after the last encoded octet (leaving a
# recoverable ``alice.smith%2B`` prefix) or stop before an encoded domain dot /
# encoded TLD (leaving the address recoverable). ``unquote`` then decodes the
# whole matched token before masking.
_ENCODED_EMAIL_RE = re.compile(
    r"(?:[\w._+\-]|%(?!40)[0-9A-Fa-f]{2})+"  # local part
    r"%40"  # @
    r"(?:(?:[\w\-]|%(?!40)[0-9A-Fa-f]{2})+(?:\.|%2[Ee]))+"  # domain labels + dot
    r"(?:[^\W\d_]|%(?!40)[0-9A-Fa-f]{2}){2,}"  # TLD (Unicode letters or encoded letters)
)


class PiiLevel(str, Enum):
    NONE = "NONE"
    BASIC = "BASIC"
    SENSITIVE = "SENSITIVE"


class PiiConfig:
    """Manages per-sink PII levels.

    Allows different PII sanitization levels for different observability sinks:
    - logs: For structured logging
    - otel: For OpenTelemetry spans
    - sentry: For Sentry error reporting
    - audit: For audit log entries
    """

    def __init__(self, levels: Optional[Dict[str, str]] = None):
        """Initialize PII configuration.

        Args:
            levels: Dictionary mapping sink names to PII levels.
                   Valid sinks: 'logs', 'otel', 'sentry', 'audit'
                   Valid levels: 'NONE', 'BASIC', 'SENSITIVE'
        """
        self._levels: Dict[str, PiiLevel] = {}

        # Start with defaults
        for sink, level_str in DEFAULT_PII_LEVELS.items():
            self._levels[sink] = PiiLevel(level_str)

        # Override with provided levels
        if levels:
            for sink, level_str in levels.items():
                if sink in DEFAULT_PII_LEVELS:
                    self._levels[sink] = PiiLevel(level_str)

    def get_level(self, sink: str) -> PiiLevel:
        """Get PII level for a specific sink.

        Args:
            sink: Sink name ('logs', 'otel', 'sentry', 'audit')

        Returns:
            PII level for the sink, or BASIC as fallback
        """
        return self._levels.get(sink, PiiLevel.BASIC)

    def set_level(self, sink: str, level: str) -> None:
        """Set PII level for a specific sink.

        Args:
            sink: Sink name ('logs', 'otel', 'sentry', 'audit')
            level: PII level ('NONE', 'BASIC', 'SENSITIVE')
        """
        if sink in DEFAULT_PII_LEVELS:
            self._levels[sink] = PiiLevel(level)


# Global PII configuration instance
_global_pii_config: Optional[PiiConfig] = None


def get_pii_config() -> PiiConfig:
    """Get the global PII configuration."""
    global _global_pii_config
    if _global_pii_config is None:
        _global_pii_config = PiiConfig()
    return _global_pii_config


def set_pii_config(config: PiiConfig) -> None:
    """Set the global PII configuration."""
    global _global_pii_config
    _global_pii_config = config


_MASK_LEVELS: frozenset[PiiLevel] = frozenset({PiiLevel.BASIC, PiiLevel.SENSITIVE})


def _mask_value(value: str) -> str:
    if not value:
        return value
    if "@" in value:
        # Only keep the domain visible when the whole value is a *bare* email —
        # otherwise everything after the ``@`` is not a plain domain (a trailing
        # secret, another URL/query, …) and masking just the local part would
        # leak it, so redact the value wholesale. Split on the *final* ``@`` so a
        # quoted local part carrying its own ``@`` (``"alice@dept"@example.com``)
        # is masked at the real separator rather than leaking ``dept``.
        if _BARE_EMAIL_RE.match(value):
            name, _, domain = value.rpartition("@")
            return f"{name[:1]}***@{domain}"
        return "***"
    return value[:2] + "***" if len(value) > 2 else "***"


def _hash_value(value: str, salt: str = "") -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()


def effective_sets(
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
) -> tuple[Set[str], Set[str], Set[str]]:
    drop = DROP_HEADERS | (extra_drop or frozenset())
    mask = MASK_FIELDS | (extra_mask or frozenset())
    hsh = HASH_FIELDS | (extra_hash or frozenset())
    return drop, mask, hsh


# Keep private alias for backward compatibility
_effective_sets = effective_sets


def sanitize_mapping(
    mapping: Mapping[str, str],
    level: PiiLevel,
    drop: Set[str],
    mask: Set[str],
    hsh: Set[str],
    hash_salt: str,
    aliases: Optional[Mapping[str, str]] = None,
) -> MutableMapping[str, str]:
    """Apply the drop/mask/hash field rules to a flat ``{key: value}`` mapping.

    ``aliases`` lets a value be governed under an additional name — the rule
    fires when *either* the mapping key or its alias is in a rule set. Used when
    the same field is reachable under more than one name (e.g. the audit
    ``remote_addr`` / ``user_agent`` columns and their ``ip`` / ``user-agent``
    semantic aliases), so an operator ``EXTRA_*`` rule keyed by either name wins.
    """
    # Normalise alias keys to lower-case up front so the per-key lookup is
    # case-insensitive: the mapping key set (``names``) is lower-cased, so an
    # alias keyed by a differently-cased column name (``Remote_Addr`` vs
    # ``remote_addr``) would otherwise silently miss and leave the value raw.
    aliases = {str(k).lower(): v for k, v in (aliases or {}).items()}
    cleaned: MutableMapping[str, str] = {}
    for key, value in mapping.items():
        key_lower = str(key).lower()
        names = {key_lower}
        alias = aliases.get(key_lower)
        if alias is not None:
            names.add(alias.lower())
        if level != PiiLevel.NONE and names & drop:
            continue
        if level in _MASK_LEVELS and names & mask:
            cleaned[key] = _mask_value(str(value))
        elif level == PiiLevel.SENSITIVE and names & hsh:
            cleaned[key] = _hash_value(str(value), hash_salt)
        else:
            cleaned[key] = value
    return cleaned


# Keep private alias for backward compatibility
_sanitize_mapping = sanitize_mapping


def sanitize_headers(
    headers: Mapping[str, str],
    level: PiiLevel,
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
    hash_salt: str = "",
) -> MutableMapping[str, str]:
    drop, mask, hsh = effective_sets(extra_drop, extra_mask, extra_hash)
    return sanitize_mapping(headers, level, drop, mask, hsh, hash_salt)


def sanitize_query_params(
    params: Mapping[str, str],
    level: PiiLevel,
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
    hash_salt: str = "",
    aliases: Optional[Mapping[str, str]] = None,
) -> MutableMapping[str, str]:
    drop, mask, hsh = effective_sets(extra_drop, extra_mask, extra_hash)
    return sanitize_mapping(params, level, drop, mask, hsh, hash_salt, aliases)


def sanitize_body(
    body: Any,
    level: PiiLevel,
    extra_drop: Optional[FrozenSet[str]] = None,
    extra_mask: Optional[FrozenSet[str]] = None,
    extra_hash: Optional[FrozenSet[str]] = None,
    hash_salt: str = "",
) -> Any:
    """Recursively sanitize a parsed body structure according to PII rules.

    Operates on the already-parsed Python structure, not raw bytes. Dicts, lists,
    and set-like databags (``tuple`` / ``set`` / ``frozenset``, which loggers and
    Sentry can attach) are traversed — the latter are normalised to lists so
    their nested dicts/leaves are sanitized (JSON has no tuple/set type, so
    serialisers coerce them anyway). Scalar values are returned as-is unless
    their *parent key* matches a PII field name.
    """
    if level == PiiLevel.NONE:
        return body
    drop, mask, hsh = effective_sets(extra_drop, extra_mask, extra_hash)
    return _sanitize_node(body, level, drop, mask, hsh, hash_salt)


def _is_pair_list(node: Any) -> bool:
    """True when ``node`` is a non-empty list/tuple of ``[key, value]`` string-keyed pairs.

    Sentry (and callers using ``set_extra`` / structured log args) can carry form
    data as a list of pairs rather than a dict. Such a node needs the key-based
    rules applied per pair; a positional array (``[1, 2]``, ``[{…}, …]``) or a
    numeric matrix must not, so every element must be a 2-item sequence whose
    first element is a ``str``.
    """
    return (
        isinstance(node, (list, tuple))
        and len(node) > 0
        and all(
            isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str)
            for item in node
        )
    )


def _sanitize_node(
    node: Any, level: PiiLevel, drop: Set[str], mask: Set[str], hsh: Set[str], hash_salt: str
) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            key_lower = str(key).lower()
            if key_lower in drop:
                continue
            if level in _MASK_LEVELS and key_lower in mask:
                result[key] = _mask_value(str(value)) if isinstance(value, str) else "***"
            elif level == PiiLevel.SENSITIVE and key_lower in hsh:
                result[key] = _hash_value(str(value), hash_salt)
            else:
                result[key] = _sanitize_node(value, level, drop, mask, hsh, hash_salt)
        return result
    if _is_pair_list(node):
        # Key/value pair form (e.g. Sentry list-of-[key, value] form data, or a
        # ``set_extra("form", [["authorization", …], ["phone", …]])``): apply the
        # field rules by each pair's key — like a dict — instead of treating the
        # list positionally, which would leave secrets under known keys raw.
        pairs: list[Any] = []
        for key, value in node:
            key_lower = str(key).lower()
            if key_lower in drop:
                continue
            if level in _MASK_LEVELS and key_lower in mask:
                pairs.append([key, _mask_value(str(value)) if isinstance(value, str) else "***"])
            elif level == PiiLevel.SENSITIVE and key_lower in hsh:
                pairs.append([key, _hash_value(str(value), hash_salt)])
            else:
                pairs.append([key, _sanitize_node(value, level, drop, mask, hsh, hash_salt)])
        return pairs
    if isinstance(node, (list, tuple, set, frozenset)):
        # Normalise tuple/set/frozenset to a list so their nested dicts/leaves
        # are sanitized; sets are unordered, so element order is arbitrary.
        return [_sanitize_node(item, level, drop, mask, hsh, hash_salt) for item in node]
    return node
