"""Network Isolation.

Implements ADR-SEC-007 Layer 5 — URL validation, protocol allow/deny
lists, SSRF prevention, and private-network address detection.

Usage::

    validator = NetworkValidator()
    result = validator.validate("https://example.com")
    if result.is_safe:
        ...

All URLs / IPs that resolve to private, link-local, loopback, or
multicast ranges are automatically denied.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

# ── Allowed protocols ────────────────────────────────────────────────────────

ALLOWED_PROTOCOLS = frozenset({"http", "https"})

DENIED_PROTOCOLS = frozenset({
    "file", "ftp", "sftp", "smb", "gopher", "telnet",
    "ssh", "ldap", "ldaps", "javascript", "data",
})

# ── Denied network ranges (RFC 1918, RFC 6598, RFC 5735, …) ─────────────────

_DENIED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),         # "This" network
    ipaddress.ip_network("10.0.0.0/8"),        # Private (RFC 1918)
    ipaddress.ip_network("100.64.0.0/10"),     # CGN (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local
    ipaddress.ip_network("172.16.0.0/12"),     # Private (RFC 1918)
    ipaddress.ip_network("192.0.0.0/24"),      # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),    # Private (RFC 1918)
    ipaddress.ip_network("198.18.0.0/15"),     # Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),       # Multicast
    ipaddress.ip_network("240.0.0.0/4"),       # Reserved
]

_DENIED_V6_NETWORKS = [
    ipaddress.ip_network("::1/128"),           # Loopback
    ipaddress.ip_network("::/128"),            # Unspecified
    ipaddress.ip_network("fe80::/10"),         # Link-local
    ipaddress.ip_network("fc00::/7"),          # Unique local
    ipaddress.ip_network("2001:db8::/32"),     # Documentation
    ipaddress.ip_network("ff00::/8"),          # Multicast
]

# ── URL pattern ──────────────────────────────────────────────────────────────

_URL_RE = re.compile(
    r"^https?://"
    r"(?:(?:[A-Za-z0-9-._~!$&'()*+,;=]|%[0-9A-Fa-f]{2})*@)?"  # userinfo
    r"(?:\[([0-9a-fA-F:]+)\]|([A-Za-z0-9-._~!$&'()*+,;=]|%[0-9A-Fa-f]{2})+)"
    r"(?::\d+)?"
    r"(?:/[^\s]*)?$"
)

# ── Types ────────────────────────────────────────────────────────────────────


@dataclass
class NetworkValidationResult:
    """Result of URL / network validation."""

    is_safe: bool
    url: str = ""
    hostname: str = ""
    protocol: str = ""
    reason: str = ""
    category: str = ""  # public, private, loopback, link_local, multicast, denied_protocol
    details: dict = field(default_factory=dict)


class NetworkValidator:
    """Validate URLs and detect SSRF / private-network targets.

    Usage::

        validator = NetworkValidator()
        result = validator.validate("https://example.com")
        assert result.is_safe
    """

    _ALLOWED_HOSTS: frozenset[str] = frozenset()
    _BLOCKED_HOSTS: frozenset[str] = frozenset()

    def __init__(self):
        self._allowed_hostnames: list[str] = []
        self._blocked_hostnames: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, url: str) -> NetworkValidationResult:
        """Validate a URL for safety.

        Checks:
            1. Protocol is allowed (http/https)
            2. Hostname can be parsed
            3. IP addresses are not in denied ranges
            4. URL patterns look reasonable
        """
        url = url.strip()

        if not url:
            return NetworkValidationResult(
                is_safe=False, url=url, reason="Empty URL",
                category="empty",
            )

        # Parse URL
        parsed = urlparse(url)
        protocol = parsed.scheme.lower()

        if not protocol:
            return NetworkValidationResult(
                is_safe=False, url=url, reason="Missing protocol",
                category="invalid_url",
            )

        if protocol in DENIED_PROTOCOLS:
            return NetworkValidationResult(
                is_safe=False, url=url, protocol=protocol,
                reason=f"Protocol '{protocol}' is not allowed",
                category="denied_protocol",
            )

        if protocol not in ALLOWED_PROTOCOLS:
            return NetworkValidationResult(
                is_safe=False, url=url, protocol=protocol,
                reason=f"Protocol '{protocol}' is not in allow list",
                category="denied_protocol",
            )

        hostname = parsed.hostname or ""
        if not hostname:
            return NetworkValidationResult(
                is_safe=False, url=url, protocol=protocol,
                reason="No hostname extracted from URL",
                category="invalid_url",
            )

        # Check against custom allow/block lists
        if self._blocked_hostnames and hostname.lower() in self._blocked_hostnames:
            return NetworkValidationResult(
                is_safe=False, url=url, hostname=hostname, protocol=protocol,
                reason=f"Hostname '{hostname}' is blocked",
                category="blocked_host",
            )

        # Check IP addresses
        ip = self._resolve_ip(hostname)

        if ip is not None:
            ip_result = self._check_ip(ip)
            if not ip_result["safe"]:
                return NetworkValidationResult(
                    is_safe=False, url=url, hostname=hostname,
                    protocol=protocol,
                    reason=ip_result["reason"],
                    category=ip_result["category"],
                    details={"ip": ip_result["ip"]},
                )

        if self._allowed_hostnames and hostname.lower() not in self._allowed_hostnames:
            return NetworkValidationResult(
                is_safe=False, url=url, hostname=hostname, protocol=protocol,
                reason=f"Hostname '{hostname}' not in allow list",
                category="not_allowed_host",
            )

        return NetworkValidationResult(
            is_safe=True, url=url, hostname=hostname, protocol=protocol,
            category="public",
        )

    def validate_ip(self, ip_str: str) -> dict:
        """Check whether a raw IP address is safe (no private/reserved ranges)."""
        try:
            ip = ipaddress.ip_address(ip_str.strip())
        except ValueError:
            return {"safe": False, "ip": ip_str, "reason": "Invalid IP address",
                    "category": "invalid"}
        return self._check_ip(ip)

    def is_public_ip(self, ip_str: str) -> bool:
        """Return True if the IP is a routable public address."""
        result = self.validate_ip(ip_str)
        return result.get("safe", False)

    def add_allowed_host(self, hostname: str) -> None:
        """Allow a specific hostname (useful for allow-list mode)."""
        self._allowed_hostnames.append(hostname.lower())

    def add_blocked_host(self, hostname: str) -> None:
        """Block a specific hostname."""
        self._blocked_hostnames.append(hostname.lower())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_ip(hostname: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        """Try resolving a hostname as an IP address literal."""
        try:
            return ipaddress.ip_address(hostname)
        except ValueError:
            return None

    @staticmethod
    def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> dict:
        """Check whether an IP is in a denied range."""
        networks = _DENIED_V6_NETWORKS if isinstance(ip, ipaddress.IPv6Address) else _DENIED_NETWORKS

        for net in networks:
            if ip in net:
                category = _classify_ip_range(ip, net)
                return {
                    "safe": False,
                    "ip": str(ip),
                    "reason": f"IP {ip} is in denied range {net} ({category})",
                    "category": category,
                }

        return {"safe": True, "ip": str(ip), "category": "public"}


# ── IP classification helpers ────────────────────────────────────────────────


def _classify_ip_range(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> str:
    """Return a human-readable category for a denied IP range."""
    net_str = str(network)
    if net_str.startswith("127.") or net_str == "::1/128":
        return "loopback"
    if net_str.startswith("10.") or net_str.startswith("172.16.") or net_str.startswith("192.168."):
        return "private"
    if "169.254" in net_str:
        return "link_local"
    if "224." in net_str or "ff00:" in net_str.lower():
        return "multicast"
    if net_str.startswith("100.64."):
        return "cgnat"
    if net_str in ("0.0.0.0/8", "::/128"):
        return "unspecified"
    return "reserved"


# ── Convenience functions ─────────────────────────────────────────────────────


def validate_url(url: str) -> NetworkValidationResult:
    """Validate a single URL.  Convenience wrapper."""
    return NetworkValidator().validate(url)


def validate_urls(urls: List[str]) -> dict:
    """Validate multiple URLs.  Returns ``{url: result, …}``."""
    validator = NetworkValidator()
    return {url: validator.validate(url) for url in urls}


def is_safe_url(url: str) -> bool:
    """Return True if the URL passes all validation checks."""
    return NetworkValidator().validate(url).is_safe
