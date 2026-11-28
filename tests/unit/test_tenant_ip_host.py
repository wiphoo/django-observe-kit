"""An IP-address Host must never be read as ``<tenant>.domain.tld``."""

from typing import Optional

import pytest
from django.test import RequestFactory, override_settings

from observe_kit.tenant import resolve_tenant_id


def _resolve(host: str) -> Optional[str]:
    with override_settings(ALLOWED_HOSTS=["*"]):
        return resolve_tenant_id(RequestFactory().get("/", HTTP_HOST=host))


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:8000",
        "10.2.3.4",
        "10.2.3.4:443",
        "127.0.0.1.",
        "127.0.0.1.:8000",
        "999.999.999.999",
        "010.002.003.004",
        "127.0.1",
        "1.2.3.4.5",
        "0x7f.0.0.1",
        "0X7F.0X0.0X0.0X1",
        "127.0.0.0x1:80",
        "[::1]",
        "[::1]:8000",
        "[2001:db8::1]",
        "[::ffff:127.0.0.1]",
        "[::ffff:10.2.3.4]:8000",
    ],
)
def test_ip_host_is_not_a_tenant(host: str) -> None:
    assert _resolve(host) is None


@pytest.mark.parametrize("host", ["::ffff:10.2.3.4", "2001:db8::10.2.3.4"])
def test_unbracketed_ipv6_is_not_a_tenant(host: str) -> None:
    """Django rejects these, but request-like objects may not validate."""

    class Request:
        META: dict[str, str] = {}

        def get_host(self) -> str:
            return host

    assert resolve_tenant_id(Request()) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "host,expected",
    [
        ("acme.example.com", "acme"),
        ("acme.example.com:8000", "acme"),
        ("acme.example.com.", "acme"),
        ("123.example.com", "123"),
        ("0x7f.example.com:80", "0x7f"),
        ("10.0.0.1.nip.io", "10"),
        ("acme.eu.example.com", "acme"),
        ("www.example.com:8000", None),
        ("example.com:8000", None),
    ],
)
def test_dns_host_tenant_unchanged(host: str, expected: Optional[str]) -> None:
    assert _resolve(host) == expected
