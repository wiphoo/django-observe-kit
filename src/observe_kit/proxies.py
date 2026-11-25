"""Trusted-proxy matching shared by client-IP and tenant-header resolution."""

from __future__ import annotations

import functools
import ipaddress
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=16)
def _parse_trusted_proxies(entries: tuple[str, ...]) -> tuple[Any, ...]:
    """Parse TRUSTED_PROXIES entries (IPs or CIDRs) into networks, once per distinct setting.

    Invalid entries are ignored with a warning: dropping an entry can only narrow
    trust, never widen it.
    """
    nets = []
    for index, entry in enumerate(entries):
        try:
            nets.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            # Log the position, not the value: keeps config text out of logs.
            logger.warning("Ignoring invalid TRUSTED_PROXIES entry at index %d", index)
    return tuple(nets)


def _is_trusted_proxy(ip: Optional[str], nets: tuple[Any, ...]) -> bool:
    """Compare on the parsed address (so ``::1`` equals ``0:0:0:0:0:0:0:1``), not the string."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr.version == n.version and addr in n for n in nets)


def is_trusted_peer(peer: Optional[str], trusted_proxies: list[str]) -> bool:
    """True when ``peer`` is covered by ``trusted_proxies`` (IPs, CIDRs, or ``["*"]``)."""
    if trusted_proxies == ["*"]:
        return True
    return _is_trusted_proxy(peer, _parse_trusted_proxies(tuple(trusted_proxies)))
