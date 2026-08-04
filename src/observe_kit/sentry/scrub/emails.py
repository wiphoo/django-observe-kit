"""Email masking backstop for the Sentry PII scrubber.

Emails are the one unambiguous PII pattern masked wherever they appear — under
any key, in free text, in a query value, or hidden behind percent-encoding
(``alice%40example.com`` and nested ``%2540`` forms). This seam owns that
masking: :func:`_mask_emails` for a single value, :func:`_mask_emails_in_leaves`
for every string leaf/key of a container. All percent-decoding goes through the
single :mod:`.decode` seam.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping
from urllib.parse import unquote

from ...pii_rules import _EMAIL_RE, _ENCODED_EMAIL_RE, _mask_value
from .constants import REDACTED as _REDACTED
from .constants import WS_SPLIT_RE as _WS_SPLIT_RE
from .decode import MAX_DECODE_PASSES as _MAX_NESTED_URL_DECODE_PASSES
from .decode import bounded_unquote as _bounded_unquote
from .decode import decode_until as _decode_until


def _mask_emails(value: Any, redact_on_exhaust: bool = True) -> Any:
    """Mask unambiguous email tokens embedded in a value, regardless of key.

    The key-based rules only mask values whose *key* is in ``MASK_FIELDS``, so an
    email under a non-sensitive key (``q=alice@example.com``, a path segment, a
    tag/extra value, …) would pass through. Emails are the one unambiguous PII
    pattern the free-text scrubber already masks, so apply the same masking here
    for consistency. A percent-encoded email (``alice%40example.com``) that never
    passed through a URL/query parser is decoded and masked too. Applied to every
    string leaf of the event by :func:`_scrub_value_fields`, so the ``@`` / ``%40``
    fast-path keeps the common no-email case cheap.

    ``redact_on_exhaust`` controls the deeply-encoded failure mode: when the
    decode cap is exhausted (or an encoded email the bounded loop couldn't reach
    is suspected) the default is to redact wholesale, but a caller that must keep
    the rest of the value (:func:`_scrub_url` masking a path) passes ``False`` to
    instead mask the email spans in the still-encoded value via
    :func:`_mask_emails_basic`, redacting only when even that finds nothing.
    """
    if not isinstance(value, str):
        return value
    # Accumulate masking across *every* supported encoding depth rather than
    # returning after the first hit — a single leaf can carry emails at different
    # depths (``alice%40example.com bob%2540example.com``), and the event walk
    # invokes this helper only once, so a shallow match must not shadow a deeper
    # one. Each pass masks literal (``@``) and singly-encoded (``%40``) emails in
    # the current form, then decodes one level to expose the next depth.
    encoded = value
    masked = False
    cap_exhausted = False
    if "@" in encoded:
        encoded, count = _EMAIL_RE.subn(lambda m: _mask_value(m.group(0)), encoded)
        masked = masked or bool(count)
    for _ in range(_MAX_NESTED_URL_DECODE_PASSES):
        if "%40" in encoded.lower():  # percent-encoded ``@``
            encoded, count = _ENCODED_EMAIL_RE.subn(
                lambda m: _mask_value(unquote(m.group(0))), encoded
            )
            masked = masked or bool(count)
        if "%" not in encoded:
            break
        # Decode only email-carrying substrings within each whitespace token,
        # preserving the original separators so newlines/tabs aren't collapsed.
        # A token is decoded only when doing so exposes a new maskable email
        # pattern (``_EMAIL_RE`` or ``_ENCODED_EMAIL_RE`` match, or a ``%40``
        # that was previously invisible) — a punctuation-adjacent encoded
        # fragment like ``alice@example.com,progress=100%25`` is not decoded
        # into ``a***@example.com,progress=100%`` because the email pattern
        # was already matchable in the original token.
        parts = _WS_SPLIT_RE.split(encoded)
        changed = False
        for i, token in enumerate(parts):
            if i % 2 == 1:  # whitespace segment
                continue
            if "%" not in token:
                continue
            decoded_token = unquote(token)
            if decoded_token == token:
                continue
            # Accept when the decoded token exposes a new email pattern.
            # Check both one-level and two-level decodes so that a double-
            # encoded domain dot (``example%252Ecom`` → ``example%2Ecom``
            # → ``example.com``) is caught.
            double_decoded = (
                unquote(decoded_token)
                if decoded_token != token and "%" in decoded_token
                else decoded_token
            )
            has_new_email = (
                (_EMAIL_RE.search(decoded_token) and not _EMAIL_RE.search(token))
                or (_ENCODED_EMAIL_RE.search(decoded_token) and not _ENCODED_EMAIL_RE.search(token))
                or (_EMAIL_RE.search(double_decoded) and not _EMAIL_RE.search(token))
                or (
                    _ENCODED_EMAIL_RE.search(double_decoded) and not _ENCODED_EMAIL_RE.search(token)
                )
                or ("%40" in decoded_token.lower() and "%40" not in token.lower())
            )
            if has_new_email:
                # Decode and mask email substrings without corrupting unrelated
                # percent-encoded data. When the regex matches the original token
                # directly, substitute only the email span. When the email is too
                # deeply encoded for the regex to see through (e.g.
                # ``alice%2540example.com`` — ``%40`` is two layers deep), decode
                # the full token to expose it; the side effect of decoding
                # adjacent encoded data (``progress=100%25`` → ``progress=100%``)
                # is acceptable compared to leaking the email.
                def _decode_mask(m: "re.Match[str]") -> str:
                    return _mask_value(unquote(m.group(0)))

                if _ENCODED_EMAIL_RE.search(token):
                    parts[i] = _ENCODED_EMAIL_RE.sub(_decode_mask, token)
                else:
                    # Decode one level at a time until an encoded email surfaces,
                    # then mask it; if none surfaces within the cap, keep the
                    # fully-decoded token. Shares the single bounded-decode seam.
                    decoded, matched = _decode_until(
                        token, lambda d: bool(_ENCODED_EMAIL_RE.search(d))
                    )
                    if matched:
                        decoded = _ENCODED_EMAIL_RE.sub(_decode_mask, decoded)
                    parts[i] = decoded
                masked = True
                changed = True
        if not changed:
            break
        encoded = "".join(parts)
        if "@" in encoded:  # a decode pass exposed a shallower-encoded email
            encoded, count = _EMAIL_RE.subn(lambda m: _mask_value(m.group(0)), encoded)
            masked = masked or bool(count)
    else:
        # Loop exhausted all iterations — the decode cap was reached while the
        # value was still changing. Redact regardless of ``masked`` so a deeply
        # encoded email behind a shallower one doesn't slip through.
        cap_exhausted = True
    if cap_exhausted:
        if not redact_on_exhaust:
            return _mask_emails_basic(value)
        return _REDACTED
    if not masked and unquote(encoded) != encoded:
        # Still percent-encoded but nothing masked above. Bounded-decode to see
        # whether the encoding actually hides an email before destroying context.
        # A lone ``%25`` — a benign *doubly-encoded percent* like
        # ``progress=100%2525`` or ``abc%2525def`` — is NOT evidence on its own:
        # one decode leaves ``%25`` and the old heuristic redacted on that alone,
        # wiping harmless captured data. Redact only once decoding reveals an
        # email indicator — a literal ``@`` (not the ``[Filtered]@`` masking
        # marker) or a matching address; ``%40`` is covered because full decoding
        # turns it into ``@`` — or the decode cap is hit while still changing (a
        # deeper email the bounded passes couldn't reach).
        decoded, exhausted = _bounded_unquote(encoded)
        if exhausted:
            return _REDACTED if redact_on_exhaust else _mask_emails_basic(value)
        if ("@" in decoded and "[Filtered]@" not in decoded) or (
            _EMAIL_RE.search(decoded) or _ENCODED_EMAIL_RE.search(decoded)
        ):
            return _REDACTED if redact_on_exhaust else _mask_emails_basic(value)
        return encoded
    return encoded if masked else value


def _mask_emails_basic(value: str) -> Any:
    """Mask emails in an encoded value with the unbounded basic regexes.

    The fallback :func:`_mask_emails` uses when it gives up on a deeply encoded
    value (decode cap exhausted, or an encoded email the bounded loop couldn't
    reach): mask the email spans in the still-encoded ``value`` directly so the
    rest is preserved — a path's unrelated ``%2F`` / ``%20`` escapes stay intact
    — and only redact wholesale when even that finds no email (a fully
    recoverable encoded address).
    """
    masked = _ENCODED_EMAIL_RE.sub(lambda m: _mask_value(unquote(m.group(0))), value)
    masked = _EMAIL_RE.sub(lambda m: _mask_value(m.group(0)), masked)
    return masked if masked != value else _REDACTED


def _mask_email_key(key: Any) -> Any:
    """Mask emails in string keys while preserving malformed non-string keys."""
    return _mask_emails(key)


def _dedupe_masked_key(mapping: Mapping[Any, Any], key: Any) -> Any:
    """Return a non-conflicting key after masking may collapse distinct keys."""
    if key not in mapping:
        return key
    if not isinstance(key, str):
        return key
    i = 2
    while f"{key}#{i}" in mapping:
        i += 1
    return f"{key}#{i}"


def _mask_emails_in_leaves(obj: Any) -> Any:
    """Mask emails in every string leaf *and* dict key of ``obj`` (no URL re-scrub), in place.

    Re-applies only the email backstop to request fields (``query_string`` /
    ``env`` / ``headers`` / a raw-body string) that were already fully
    URL/query-scrubbed by the request block and kept out of the whole-event walk
    — so their nested, already-hashed params aren't hashed a second time, while a
    plain email in an otherwise-unscrubbed value is still masked. Dict keys are
    also masked, matching the per-parameter key masking in :func:`_scrub_query_string`.

    Container types are covered consistently (dict/list mutated in place,
    tuple/set/frozenset rebuilt) so an email in a set-valued saved field is
    masked the same way the other recursive walkers handle it.
    """
    if isinstance(obj, str):
        return _mask_emails(obj)
    if isinstance(obj, dict):
        rebuilt: Dict[Any, Any] = {}
        for key, value in list(obj.items()):
            masked_key = _dedupe_masked_key(rebuilt, _mask_email_key(key))
            rebuilt[masked_key] = _mask_emails_in_leaves(value)
        obj.clear()
        obj.update(rebuilt)
    elif isinstance(obj, (list, tuple)):
        rebuilt_list = [_mask_emails_in_leaves(item) for item in obj]
        if isinstance(obj, list):
            obj[:] = rebuilt_list
        else:
            obj = tuple(rebuilt_list)
    elif isinstance(obj, (set, frozenset)):
        obj = type(obj)(_mask_emails_in_leaves(item) for item in obj)
    return obj
