"""Credentials of a URL nested in (or prefixed inside) a URL-typed field are redacted.

``_scrub_url`` used to strip ``user:pass@`` only from the outer authority and leave
the path to the email backstop, which does not match a numeric / non-domain host.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config

SECRET = "hunter2pw"
CRED = f"https://bob:{SECRET}@10.0.0.5/p"
SAFE = "https://[Filtered]@10.0.0.5/p"
NESTED, NESTED_SAFE = f"https://site.test/r/{CRED}", f"https://site.test/r/{SAFE}"
PATH, PATH_SAFE = f"/r/{CRED}", f"/r/{SAFE}"
PREFIXES = ("'", "(", "a,", "redirect:", "see ")


def _scrub(event: Dict[str, Any]) -> Dict[str, Any]:
    out = config.scrub_event(event, None, level=PiiLevel.SENSITIVE, hash_salt="s")
    assert out is not None
    return out


def _frame_vars(value: str) -> Dict[str, Any]:
    return {"exception": {"values": [{"stacktrace": {"frames": [{"vars": {"url": value}}]}}]}}


CASES = [
    pytest.param({"request": {"url": NESTED}}, {"request": {"url": NESTED_SAFE}}, id="request-url"),
    pytest.param(
        {"request": {"env": {"PATH_INFO": PATH, "REQUEST_URI": PATH}}},
        {"request": {"env": {"PATH_INFO": PATH_SAFE, "REQUEST_URI": PATH_SAFE}}},
        id="env-path-info-request-uri",
    ),
    pytest.param(
        {"request": {"headers": {"Referer": NESTED}}},
        {"request": {"headers": {"Referer": NESTED_SAFE}}},
        id="referer-header",
    ),
    pytest.param(
        {"spans": [{"op": "http.client", "data": {"http.url": NESTED}}]},
        {"spans": [{"op": "http.client", "data": {"http.url": NESTED_SAFE}}]},
        id="span-http-url",
    ),
    pytest.param(
        {"request": {"url": NESTED.replace("10.0.0.5", "host.test")}},
        {"request": {"url": NESTED_SAFE.replace("10.0.0.5", "host.test")}},
        id="domain-host-not-partially-masked",
    ),
    *(
        pytest.param(
            {"request": {"url": prefix + CRED}},
            {"request": {"url": prefix + SAFE}},
            id=f"request-url-prefix-{prefix!r}",
        )
        for prefix in PREFIXES
    ),
    pytest.param(
        {"request": {"data": {"url": "'" + CRED}}},
        {"request": {"data": {"url": "'" + SAFE}}},
        id="request-data-url",
    ),
    pytest.param(
        {"breadcrumbs": {"values": [{"data": {"url": "'" + CRED}}]}},
        {"breadcrumbs": {"values": [{"data": {"url": "'" + SAFE}}]}},
        id="breadcrumb-data-url",
    ),
    pytest.param(_frame_vars("'" + CRED), _frame_vars("'" + SAFE), id="frame-vars-url"),
]


@pytest.mark.parametrize(("event", "expected"), CASES)
def test_nested_url_credentials_are_redacted(
    event: Dict[str, Any], expected: Dict[str, Any]
) -> None:
    out = _scrub(copy.deepcopy(event))
    assert SECRET not in repr(out)
    assert out == expected
    assert _scrub(copy.deepcopy(out)) == out  # idempotent


@pytest.mark.parametrize(
    "url",
    [
        "https://site.test/r/p",
        "https://site.test/a//b/c?x=1",
        "https://site.test/r/https://10.0.0.5/p",
        "https://site.test/a//b//c//d//e//f//g//h//i//j//k//l//m//n//o//p//q//r//s//t//u//v/w",
        "/r/p",
    ],
)
def test_url_without_nested_credentials_is_unchanged(url: str) -> None:
    assert _scrub({"request": {"url": url}}) == {"request": {"url": url}}
