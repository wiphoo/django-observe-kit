"""The email backstop must never emit a decoded token it did not scrub.

``_mask_emails`` percent-decodes a token looking for a hidden email. It used to
emit the fully-decoded token even when no email surfaced, turning a hidden
double-encoded ``scheme://user:pass@host`` credential into plaintext in the
scrubber's own output.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable, Dict
from urllib.parse import quote, unquote

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config
from observe_kit.sentry.scrub.emails import _mask_emails

SECRET = "hunter2pw"
NESTED_URL = "https://site.test/r/{}", f"https://bob:{SECRET}@10.0.0.5/p"
FREE_TEXT = "{}", f"msg: redis://bob:{SECRET}@10.0.0.5/p"
DEPTHS = range(7)

LOCATIONS: Dict[str, Callable[[str], Dict[str, Any]]] = {
    "extra": lambda v: {"extra": {"k": v}},
    "tags": lambda v: {"tags": {"k": v}},
    "contexts": lambda v: {"contexts": {"c": {"k": v}}},
    "request.url": lambda v: {"request": {"url": v}},
    "referer": lambda v: {"request": {"headers": {"Referer": v}}},
    "custom-header-dict": lambda v: {"request": {"headers": {"X-Custom": v}}},
    "custom-header-pairs": lambda v: {"request": {"headers": [["X-Custom", v]]}},
    "env": lambda v: {"request": {"env": {"K": v}}},
    "data-dict": lambda v: {"request": {"data": {"k": v}}},
    "data-json": lambda v: {"request": {"data": json.dumps({"k": v})}},
}
# An *un-encoded* credentialed URL in a URL-typed field is the separate
# ``_scrub_url`` path-handling gap, not this decode-emit root cause.
URL_TYPED = {"request.url", "referer"}


def _encode(value: str, depth: int) -> str:
    for _ in range(depth):
        value = quote(value, safe="")
    return value


def _fully_decoded(value: str) -> str:
    for _ in range(10):
        value = unquote(value)
    return value


def _scrub(event: Dict[str, Any]) -> Dict[str, Any]:
    result = config.scrub_event(event, None, level=PiiLevel.SENSITIVE, hash_salt="s")
    assert result is not None
    return result


CASES = [
    pytest.param(
        location, template.format(_encode(secret, depth)), id=f"{name}-{location}-d{depth}"
    )
    for name, (template, secret) in (("nested-url", NESTED_URL), ("free-text", FREE_TEXT))
    for location in LOCATIONS
    for depth in DEPTHS
    if not (depth == 0 and location in URL_TYPED)
]


@pytest.mark.parametrize(("location", "value"), CASES)
def test_encoded_credential_never_survives(location: str, value: str) -> None:
    once = _scrub(LOCATIONS[location](value))
    rendered = repr(once)
    assert SECRET not in rendered
    assert SECRET not in _fully_decoded(rendered)  # an encoded survivor is still a leak
    assert _scrub(copy.deepcopy(once)) == once  # idempotent


@pytest.mark.parametrize("depth", DEPTHS)
def test_mask_emails_does_not_emit_decoded_credential(depth: int) -> None:
    value = _encode(NESTED_URL[1], depth)
    out = _mask_emails(value)
    # Not this helper's job to scrub URL credentials — but it must not decode them.
    assert out in (value, "[Filtered]")


@pytest.mark.parametrize(
    "value", ["a%2520b", "progress=100%2525", "x%253Dy%2526z", "q=a%252Fb%2520c", "100%25"]
)
@pytest.mark.parametrize("location", list(LOCATIONS))
def test_benign_encoded_value_is_not_emitted_more_decoded(location: str, value: str) -> None:
    # No secret to scrub, so the value must come back verbatim — never as a
    # more-decoded form of itself.
    assert value in repr(_scrub(LOCATIONS[location](value)))


def test_encoded_emails_are_still_masked() -> None:
    assert _mask_emails("alice%40example.com") == "a***@example.com"
    assert _mask_emails("alice%2540example.com") == "a***@example.com"
    assert _mask_emails("alice%40example%252Ecom") == "a***@example.com"
