"""F4: ``X-Tenant-ID`` is opt-in, trusted-proxy-only and validated."""

from __future__ import annotations

from typing import Any, Optional

import pytest
from django.test import RequestFactory

from observe_kit.metrics.prometheus import _reset_label_guards_for_tests, guard_tenant_label
from observe_kit.tenant import resolve_tenant_id

ON = {"TENANT_HEADER_TRUSTED": True, "TRUSTED_PROXIES": ["10.0.0.1"]}


def _req(
    header: Optional[str] = "victim",
    addr: str = "10.0.0.1",
    host: str = "example.com",
    tenant_id: Optional[str] = None,
) -> Any:
    extra: dict[str, str] = {"REMOTE_ADDR": addr, "HTTP_HOST": host}
    if header is not None:
        extra["HTTP_X_TENANT_ID"] = header
    request = RequestFactory().get("/", **extra)
    request.tenant = type("T", (), {"id": tenant_id})() if tenant_id else None  # type: ignore[attr-defined]
    return request


def test_header_ignored_by_default() -> None:
    assert resolve_tenant_id(_req()) is None


def test_header_ignored_when_enabled_but_no_trusted_proxies(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"TENANT_HEADER_TRUSTED": True}):
        assert resolve_tenant_id(_req()) is None


def test_header_ignored_when_peer_untrusted(observe_kit_settings: Any) -> None:
    with observe_kit_settings(ON):
        assert resolve_tenant_id(_req(addr="203.0.113.9")) is None


def test_header_honoured_when_enabled_and_peer_trusted(observe_kit_settings: Any) -> None:
    with observe_kit_settings(ON):
        assert resolve_tenant_id(_req()) == "victim"


def test_header_honoured_with_wildcard_proxies(observe_kit_settings: Any) -> None:
    with observe_kit_settings({"TENANT_HEADER_TRUSTED": True, "TRUSTED_PROXIES": ["*"]}):
        assert resolve_tenant_id(_req(addr="203.0.113.9")) == "victim"


def test_request_tenant_beats_header(observe_kit_settings: Any) -> None:
    with observe_kit_settings(ON):
        assert resolve_tenant_id(_req(tenant_id="real")) == "real"


def test_header_beats_subdomain_when_trusted(observe_kit_settings: Any) -> None:
    with observe_kit_settings(ON):
        assert resolve_tenant_id(_req(host="acme.example.com")) == "victim"


def test_subdomain_used_when_header_untrusted() -> None:
    assert resolve_tenant_id(_req(host="acme.example.com")) == "acme"


@pytest.mark.parametrize(
    "bad", ["a" * 129, "has space", "semi;colon", "slash/x", "unié", "<script>", "a\tb"]
)
def test_invalid_header_rejected_not_truncated(bad: str, observe_kit_settings: Any) -> None:
    with observe_kit_settings(ON):
        assert resolve_tenant_id(_req(header=bad)) is None


def test_max_length_header_accepted(observe_kit_settings: Any) -> None:
    with observe_kit_settings(ON):
        assert resolve_tenant_id(_req(header="a" * 128)) == "a" * 128


def test_junk_headers_do_not_consume_label_cap(observe_kit_settings: Any) -> None:
    _reset_label_guards_for_tests()
    with observe_kit_settings({"METRICS_MAX_LABEL_CARDINALITY": 1000}):
        for i in range(1000):
            tenant = resolve_tenant_id(_req(header=f"junk-{i}"))
            if tenant:
                guard_tenant_label(tenant)
        assert guard_tenant_label("real-tenant") == "real-tenant"
    _reset_label_guards_for_tests()


@pytest.mark.parametrize(
    ("addr", "proxies", "expected"),
    [
        ("10.1.2.3", ["10.0.0.0/8"], "victim"),
        ("2001:db8::7", ["2001:db8::/32"], "victim"),
        ("0:0:0:0:0:0:0:1", ["::1"], "victim"),
        ("11.0.0.1", ["10.0.0.0/8"], None),
    ],
)
def test_header_trust_honours_cidr_and_ipv6(
    observe_kit_settings: Any, addr: str, proxies: list[str], expected: Optional[str]
) -> None:
    with observe_kit_settings({"TENANT_HEADER_TRUSTED": True, "TRUSTED_PROXIES": proxies}):
        assert resolve_tenant_id(_req(addr=addr)) == expected
