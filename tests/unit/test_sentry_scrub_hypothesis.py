"""Property-based tests for the Sentry PII scrubber (issue #96, L3a).

These lock the scrubber's core security guarantee — *a raw secret must not
survive scrubbing, at any percent-encoding depth* — before the URL-handling
rewrite (#98/#99) restructures how that guarantee is enforced. They target the
current scrubber via the public ``scrub_event`` entry point.

The secret is always a high-entropy lowercase-alnum sentinel (never a regex
metachar, and never percent-encoded by ``quote`` since alnum is always safe), so
"the sentinel appears anywhere in the scrubbed output" is an unambiguous leak
signal — masking keeps at most the first character, and redaction removes it
entirely.
"""

from __future__ import annotations

import json
import string
from datetime import timedelta
from typing import Any
from urllib.parse import quote, unquote

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Mirror the sibling `test_sentry_scrub.py` guard so the two Sentry-scrub modules
# collect identically: nothing under ``observe_kit.sentry`` (which requires
# Django) is imported at module top level — the Sentry imports are lazy, inside
# the helpers/tests, so collection succeeds and the skip applies when Django is
# absent. A generous finite deadline keeps Hypothesis' per-example runtime
# guardrail without flaking on the cold first example (import warmup).
pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)
_HYP = settings(deadline=timedelta(milliseconds=1000), max_examples=150)

# Upper bound for the email-encoding depth strategy. Must stay > the scrubber's
# decode cap so the exhaustion→redact path is exercised; kept as a literal (not a
# top-level import from the Sentry package) to avoid loading Django at collection
# time, and cross-checked against the real cap at runtime in the test body.
_MAX_ENCODING_DEPTH = 6

# High-entropy, metachar-free secret. Length >= 8 guarantees that a masked form
# (first char + "***") cannot contain the whole sentinel.
_SENTINEL = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=8, max_size=16)


def _scrub(event: dict[str, Any], level: str = "SENSITIVE") -> dict[str, Any]:
    from observe_kit.pii_rules import PiiLevel
    from observe_kit.sentry.config import scrub_event

    return scrub_event(event, None, getattr(PiiLevel, level), hash_salt="pepper")


def _encode(value: str, depth: int) -> str:
    """Percent-encode ``value`` ``depth`` times (``@`` -> ``%40`` -> ``%2540`` ...)."""
    for _ in range(depth):
        value = quote(value, safe="")
    return value


def _deep_unquote(value: str) -> str:
    for _ in range(8):
        nxt = unquote(value)
        if nxt == value:
            return value
        value = nxt
    return value


def _blob(event: Any) -> str:
    """Serialize every leaf of the scrubbed event into one searchable string."""
    return json.dumps(event, default=str, ensure_ascii=False)


def _assert_absent(sentinel: str, event: Any) -> None:
    blob = _blob(event)
    assert sentinel not in blob, f"raw secret leaked: {blob!r}"
    # Also fail if it only survived in still-encoded form.
    assert sentinel not in _deep_unquote(blob), f"secret survived encoded: {blob!r}"


@_HYP
@given(
    sentinel=_SENTINEL,
    # Cover depths at and *beyond* the decode cap: within the cap the value is
    # decoded and masked, at/over the cap the exhaustion path must redact it
    # wholesale — either way the secret must not survive.
    depth=st.integers(min_value=0, max_value=_MAX_ENCODING_DEPTH),
    placement=st.sampled_from(["message", "extra", "query", "url_query"]),
)
def test_email_secret_never_survives_any_depth(sentinel: str, depth: int, placement: str) -> None:
    """An email's local part never survives, at any placement or encoding depth."""
    # Lazy import (Django-dependent) — keeps the Sentry package out of module
    # collection. Cross-check that the strategy bound still exceeds the cap.
    from observe_kit.sentry.scrub.decode import MAX_DECODE_PASSES

    assert _MAX_ENCODING_DEPTH > MAX_DECODE_PASSES, "depth bound must exceed the decode cap"
    token = _encode(f"{sentinel}@example.com", depth)
    event: dict[str, Any]
    if placement == "message":
        event = {"message": f"user {token} signed in"}
    elif placement == "extra":
        event = {"extra": {"note": token}}
    elif placement == "query":
        event = {"request": {"query_string": f"q={token}"}}
    else:
        event = {"request": {"url": f"https://app.test/search?q={token}"}}
    _assert_absent(sentinel, _scrub(event))


@_HYP
@given(
    user=_SENTINEL,
    password=_SENTINEL,
    placement=st.sampled_from(["url", "message", "query", "referer_header"]),
)
def test_url_userinfo_credential_never_survives(user: str, password: str, placement: str) -> None:
    """The *entire* ``user:password@`` userinfo is removed, wherever the URL sits.

    Both halves are high-entropy sentinels: since ``password@host`` is itself a
    valid email, asserting only the password would pass even if the email
    backstop masked just that and left ``user:`` behind. Requiring *both* to be
    absent proves wholesale userinfo removal, not incidental email masking.
    """
    url = f"https://{user}:{password}@internal.test/dashboard"
    event: dict[str, Any]
    if placement == "url":
        event = {"request": {"url": url}}
    elif placement == "message":
        event = {"message": f"redirecting to {url} shortly"}
    elif placement == "query":
        event = {"request": {"query_string": f"next={url}"}}
    else:
        event = {"request": {"headers": {"Referer": url}}}
    scrubbed = _scrub(event)
    _assert_absent(user, scrubbed)
    _assert_absent(password, scrubbed)
