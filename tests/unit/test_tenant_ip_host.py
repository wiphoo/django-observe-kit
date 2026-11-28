"""An IP-address Host must never be read as ``<tenant>.domain.tld``."""

from typing import Optional

import pytest
from django.test import RequestFactory, override_settings

from observe_kit.tenant import resolve_tenant_id


def _resolve(host: str) -> Optional[str]:
    with override_settings(ALLOWED_HOSTS=["*"]):
        return resolve_tenant_id(RequestFactory().get("/", HTTP_HOST=host))


class _HostOnly:
    """A request-like object that, unlike Django, does not validate the host."""

    META: dict[str, str] = {}

    def __init__(self, host: str) -> None:
        self.host = host

    def get_host(self) -> str:
        return self.host


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

    assert resolve_tenant_id(_HostOnly(host)) is None  # type: ignore[arg-type]


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


@pytest.mark.parametrize(
    "label", ["a" * 128, "my_tenant", "xn--mnchen-3ya", "XN--Mnchen-3ya", "a-1"]
)
def test_valid_host_label_is_a_tenant(label: str) -> None:
    # Not through Django: some of these checks must not depend on its host validation.
    assert resolve_tenant_id(_HostOnly(f"{label}.example.com")) == label  # type: ignore[arg-type]


@pytest.mark.parametrize("label", ["a" * 129, "a" * 200, "", "münchen", "a b", "a\n"])
def test_invalid_host_label_is_ignored_not_truncated(label: str) -> None:
    assert resolve_tenant_id(_HostOnly(f"{label}.example.com")) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "label,expected",
    [("a" * 128, "a" * 128), ("a" * 129, None), ("xn--mnchen-3ya", "xn--mnchen-3ya")],
)
def test_host_label_length_through_django(label: str, expected: Optional[str]) -> None:
    assert _resolve(f"{label}.example.com") == expected
