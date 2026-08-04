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
from typing import Any
from urllib.parse import quote, unquote

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)

# High-entropy, metachar-free secret. Length >= 8 guarantees that a masked form
# (first char + "***") cannot contain the whole sentinel.
_SENTINEL = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=8, max_size=16)


def _scrub(event: dict, level: str = "SENSITIVE") -> dict:
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


@settings(deadline=None, max_examples=150)
@given(
    sentinel=_SENTINEL,
    depth=st.integers(min_value=0, max_value=3),
    placement=st.sampled_from(["message", "extra", "query", "url_query"]),
)
def test_email_secret_never_survives_any_depth(sentinel: str, depth: int, placement: str) -> None:
    """An email's local part never survives, at any placement or encoding depth."""
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


@settings(deadline=None, max_examples=100)
@given(
    sentinel=_SENTINEL,
    placement=st.sampled_from(["url", "message", "query", "referer_header"]),
)
def test_url_userinfo_credential_never_survives(sentinel: str, placement: str) -> None:
    """A ``user:password@`` credential in a URL is always redacted, wherever the URL sits."""
    url = f"https://admin:{sentinel}@internal.test/dashboard"
    event: dict[str, Any]
    if placement == "url":
        event = {"request": {"url": url}}
    elif placement == "message":
        event = {"message": f"redirecting to {url} shortly"}
    elif placement == "query":
        event = {"request": {"query_string": f"next={url}"}}
    else:
        event = {"request": {"headers": {"Referer": url}}}
    _assert_absent(sentinel, _scrub(event))
