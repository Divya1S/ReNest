"""Safety checks for URLs the server fetches on someone else's say-so.

Whenever a user or a partner supplies a URL and the backend requests it, that
request is made from inside the deployment's network. Unchecked, it can be
aimed at the cloud metadata service, the loopback interface or a private
service that trusts its neighbours (server-side request forgery).

The rules live in this one module so the vision image fetch and the partner
webhook registration cannot drift apart:

  * https only
  * the host must resolve, and every address it resolves to must be public
  * callers never follow redirects (a redirect would skip these checks)

Resolution here and the later connection are two separate lookups, so a
hostile DNS server can still answer differently the second time. Restricting
fetches to a known media origin (`url_is_under_base`) closes that gap wherever
such an origin is configured.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Union
from urllib.parse import SplitResult, urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

_DEFAULT_PORTS = {"http": 80, "https": 443}


def is_public_address(address: IPAddress) -> bool:
    """True when `address` is routable on the public internet."""
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        # ::ffff:127.0.0.1 reaches IPv4 loopback through a dual-stack socket,
        # so judge the address it really points at.
        address = mapped
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return False
    # Catches what the flags above leave out, such as carrier-grade NAT space
    # (100.64.0.0/10), which some clouds use for internal services.
    return bool(address.is_global)


def resolve_host(host: str) -> list[IPAddress]:
    """Every address `host` resolves to. Raises OSError when it does not resolve."""
    addresses: list[IPAddress] = []
    for info in socket.getaddrinfo(host, None):
        raw = str(info[4][0])
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError as exc:
            raise OSError(f"Unparseable address for host: {raw!r}") from exc
    if not addresses:
        raise OSError("Host resolved to no addresses.")
    return addresses


def public_https_url_error(url: str, *, noun: str = "URL") -> str | None:
    """Why `url` is not a safe public https target, or None when it is.

    `noun` names the thing being fetched in the message ("webhook", "image").
    Every resolved address is checked, not just the first: a host with one
    public and one private record would otherwise pass and still be reachable
    on the private one.
    """
    try:
        URLValidator(schemes=["https"])(url)
    except DjangoValidationError:
        return "Enter a valid https:// URL."

    host = urlsplit(url).hostname or ""
    try:
        addresses = resolve_host(host)
    except (OSError, UnicodeError):
        return f"The {noun} host could not be resolved."
    if not all(is_public_address(address) for address in addresses):
        return f"The {noun} host must be a public address."
    return None


def _port(parts: SplitResult) -> int | None:
    return parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())


def url_is_under_base(url: str, base_url: str) -> bool:
    """True when `url` points inside `base_url` (same origin, path beneath it).

    Compared component by component rather than with str.startswith, which
    accepts "https://cdn.example.com.evil.test/" for the base
    "https://cdn.example.com".
    """
    try:
        target, base = urlsplit(url), urlsplit(base_url)
        same_origin = (
            target.scheme.lower() == base.scheme.lower()
            and bool(base.hostname)
            and (target.hostname or "").lower() == (base.hostname or "").lower()
            and _port(target) == _port(base)
        )
    except ValueError:
        return False
    if not same_origin:
        return False
    path = target.path
    # Dot segments (plain or percent-encoded) are resolved by the server, so
    # "/bucket/../other" would escape a path-scoped base.
    if "%2e" in path.lower() or any(segment in {".", ".."} for segment in path.split("/")):
        return False
    return path.startswith(base.path.rstrip("/") + "/")
