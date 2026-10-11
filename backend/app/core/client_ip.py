"""Client address resolution behind trusted reverse proxies, and the identity used for limits.

`X-Forwarded-For` is client-controlled: anyone can send it. It is only believed when the direct
peer is a configured trusted proxy, and then only the right-most address that no trusted proxy
added is used — every address to its left could have been forged by the client.
"""

import ipaddress
from collections.abc import Sequence

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN_CLIENT = "unknown"
# IPv6 hosts usually control a whole /64, so per-address limits would be trivially rotated.
IPV6_IDENTITY_PREFIX = 64


def _parse_address(value: str) -> IPAddress | None:
    """The address in `value`, with IPv4-mapped IPv6 unwrapped to IPv4; None if not an IP."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted(address: IPAddress, trusted: Sequence[IPNetwork]) -> bool:
    # Membership across IP versions is simply False, never an error.
    return any(address in network for network in trusted)


def resolve_client_ip(
    peer: str | None, x_forwarded_for: str | None, trusted: Sequence[IPNetwork]
) -> str:
    """The originating client's address.

    - No peer -> `"unknown"`.
    - Peer not a trusted proxy -> the peer; `X-Forwarded-For` is ignored.
    - Peer trusted -> walk `X-Forwarded-For` right to left, skipping trusted proxies and
      entries that are not IP addresses, and return the first untrusted address. If there is
      none, the peer itself is returned.
    """
    if not peer:
        return UNKNOWN_CLIENT
    peer_address = _parse_address(peer)
    if peer_address is None or not _is_trusted(peer_address, trusted):
        return peer
    for entry in reversed((x_forwarded_for or "").split(",")):
        address = _parse_address(entry)
        if address is None or _is_trusted(address, trusted):
            continue
        return str(address)
    return peer


def client_identity(ip: str) -> str:
    """The key used for rate limits and the login throttle.

    IPv4 addresses are used as-is, IPv4-mapped IPv6 becomes its IPv4 address, other IPv6
    addresses become their /64 network (e.g. `"2001:db8:1:2::/64"`). Non-IP values such as
    `"unknown"` are returned unchanged.
    """
    address = _parse_address(ip)
    if address is None:
        return ip
    if isinstance(address, ipaddress.IPv4Address):
        return str(address)
    network = ipaddress.IPv6Network((int(address), IPV6_IDENTITY_PREFIX), strict=False)
    return str(network)
