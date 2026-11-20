"""Whole-event golden snapshot of ``scrub_event`` (characterization for #100).

The event touches every traversal phase (bytes leaves/keys, request pieces, tags,
extra, contexts, user, logentry params, breadcrumbs, spans, stack vars, tuples/sets,
pair lists). The snapshot was recorded from the pre-refactor multi-pass
implementation; the single-walk refactor must reproduce it exactly.
Regenerate (only for an intentional behaviour change) with
``UPDATE_GOLDEN=1 pytest tests/unit/test_sentry_scrub_golden.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from observe_kit.pii_rules import PiiLevel
from observe_kit.sentry import config

_GOLDEN = Path(__file__).parent / "data" / "sentry_scrub_golden.json"
_URL = "https://user:pw@example.com/p?phone=0812345678&next=/s%3Ftoken%3Dabc&ip=192.0.2.1"


def _event() -> Dict[str, Any]:
    return {
        "server_name": "host-alice@example.com",
        "release": "1.0",
        "message": f"failed for alice@example.com at {_URL}",
        "transaction": "/search?phone=0812345678",
        "logentry": {
            "message": "u=%s",
            "params": ("alice@example.com", {"phone": "0812345678", "db.statement": "SELECT 1"}),
        },
        "request": {
            "url": _URL,
            "method": "POST",
            "headers": {
                "Authorization": "Bearer x",
                b"X-Real-IP": b"192.0.2.1",
                "Referer": "https://h/p?phone=0812345678",
                "X-Next": "/search?phone=0812345678",
                "b'phone'": "0812345678",
            },
            "cookies": {"sid": "abc"},
            "fragment": "access_token=abc",
            "query_string": "phone=0812345678&token=a&token=b&q=alice@example.com",
            "data": {"phone": "0812345678", "url": _URL, "items": ({"phone": "1"},), "k": b"bytes"},
            "env": {
                "REMOTE_ADDR": "192.0.2.1",
                "HTTP_AUTHORIZATION": "x",
                "QUERY_STRING": "phone=0812345678",
                "REQUEST_URI": "/s?phone=0812345678",
                "HTTP_X_NEXT": "/search?phone=0812345678",
            },
        },
        "tags": {"phone": "0812345678", "who": "alice@example.com"},
        "extra": {
            "url": _URL,
            "raw": b"alice@example.com",
            "cc": {"alice@example.com"},
            "pairs": [["phone", "0812345678"], ["http.path", "/s?phone=0812345678"]],
            "nested": {"params": {"ip": "192.0.2.1"}, "list": ["/s?phone=0812345678", 3]},
            "db.query.parameter.x": ["0812345678"],
        },
        "contexts": {"c": {"email": "alice@example.com", "ip": "192.0.2.1"}},
        "user": {"email": "alice@example.com", "ip_address": "192.0.2.1", "id": "1"},
        "breadcrumbs": {
            "values": [
                {
                    "category": "alice@example.com",
                    "message": f"GET {_URL}",
                    "data": {"phone": "0812345678", "url": _URL},
                }
            ]
        },
        "spans": [
            {
                "op": "http.client",
                "description": f"GET {_URL} alice@example.com",
                "data": {"http.url": _URL, "http.query": "phone=0812345678"},
                "tags": {"phone": "0812345678"},
            },
            {"op": "db.query", "description": "SELECT * FROM t WHERE p='0812345678'"},
        ],
        "exception": {
            "values": [
                {
                    "value": "boom alice@example.com",
                    "stacktrace": {"frames": [{"vars": {"phone": "0812345678", "e": "a@b.co"}}]},
                }
            ]
        },
    }


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: sorted(map(str, o)), sort_keys=True))


@pytest.mark.parametrize("level", [PiiLevel.BASIC, PiiLevel.SENSITIVE])
@pytest.mark.parametrize(
    "ops",
    [
        {},
        {"extra_mask": frozenset({"server_name", "category", "op"})},
        {"extra_hash": frozenset({"release", "ip_address"}), "extra_drop": frozenset({"id"})},
    ],
    ids=["plain", "mask", "hash-drop"],
)
def test_scrub_event_matches_golden(level: PiiLevel, ops: Dict[str, Any]) -> None:
    out = _jsonable(config.scrub_event(_event(), None, level, "salt", **ops))
    key = f"{level.name}:{sorted(ops)}"
    golden = json.loads(_GOLDEN.read_text()) if _GOLDEN.exists() else {}
    if os.environ.get("UPDATE_GOLDEN"):
        golden[key] = out
        _GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    assert out == golden[key]
