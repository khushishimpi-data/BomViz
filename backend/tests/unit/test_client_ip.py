import ipaddress

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.client_ip import IPNetwork, client_identity, resolve_client_ip
from app.core.dependencies import ClientIpDep
from tests.conftest import make_settings

PROXY = "172.30.0.10"
TRUSTED: list[IPNetwork] = [ipaddress.ip_network(PROXY)]


def test_missing_peer_is_unknown() -> None:
    assert resolve_client_ip(None, "1.2.3.4", TRUSTED) == "unknown"


def test_untrusted_peer_ignores_forged_forwarded_for() -> None:
    assert resolve_client_ip("198.51.100.7", "1.2.3.4", TRUSTED) == "198.51.100.7"


def test_nothing_is_trusted_by_default() -> None:
    assert resolve_client_ip(PROXY, "1.2.3.4", []) == PROXY


def test_trusted_peer_uses_the_forwarded_client() -> None:
    assert resolve_client_ip(PROXY, "203.0.113.5", TRUSTED) == "203.0.113.5"


def test_trusted_peer_uses_right_most_address_so_client_prefix_cannot_spoof() -> None:
    # The client sent "X-Forwarded-For: 1.2.3.4"; the proxy appended the real address.
    assert resolve_client_ip(PROXY, "1.2.3.4, 203.0.113.5", TRUSTED) == "203.0.113.5"


def test_chain_of_trusted_proxies_is_skipped() -> None:
    trusted: list[IPNetwork] = [ipaddress.ip_network("10.0.0.0/8"), *TRUSTED]

    assert (
        resolve_client_ip(PROXY, "1.2.3.4, 203.0.113.5, 10.1.2.3, 10.9.9.9", trusted)
        == "203.0.113.5"
    )


@pytest.mark.parametrize(
    "header", ["203.0.113.5, not-an-ip", "203.0.113.5,,  ", "203.0.113.5, 1.2.3.4:80, [::1]"]
)
def test_garbage_forwarded_entries_are_skipped(header: str) -> None:
    assert resolve_client_ip(PROXY, header, TRUSTED) == "203.0.113.5"


@pytest.mark.parametrize("header", [None, "", "garbage", PROXY])
def test_trusted_peer_without_usable_forwarded_address_falls_back_to_peer(
    header: str | None,
) -> None:
    assert resolve_client_ip(PROXY, header, TRUSTED) == PROXY


def test_ipv4_mapped_trusted_peer_is_recognised() -> None:
    assert resolve_client_ip(f"::ffff:{PROXY}", "203.0.113.5", TRUSTED) == "203.0.113.5"


def test_ipv4_identity_is_unchanged() -> None:
    assert client_identity("203.0.113.5") == "203.0.113.5"


@pytest.mark.parametrize(
    "ip", ["2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:DB8:1:2:0:0:0:abcd"]
)
def test_ipv6_identity_is_its_64_network(ip: str) -> None:
    assert client_identity(ip) == "2001:db8:1:2::/64"


def test_ipv6_addresses_in_different_64s_have_different_identities() -> None:
    assert client_identity("2001:db8:1:2::1") != client_identity("2001:db8:1:3::1")


def test_ipv4_mapped_ipv6_identity_is_the_ipv4_address() -> None:
    assert client_identity("::ffff:203.0.113.5") == "203.0.113.5"


@pytest.mark.parametrize("value", ["unknown", "testclient"])
def test_non_ip_identity_is_unchanged(value: str) -> None:
    assert client_identity(value) == value


async def _identity_seen(peer: str, headers: list[tuple[str, str]], trusted: str) -> str:
    app = FastAPI()
    app.state.settings = make_settings(trusted_proxy_ips=trusted)

    @app.get("/whoami")
    async def whoami(client_ip: ClientIpDep) -> dict[str, str]:
        return {"client": client_ip}

    transport = ASGITransport(app=app, client=(peer, 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/whoami", headers=headers)
    client_value: str = response.json()["client"]
    return client_value


async def test_dependency_ignores_forwarded_for_from_untrusted_peer() -> None:
    seen = await _identity_seen("198.51.100.7", [("X-Forwarded-For", "1.2.3.4")], PROXY)

    assert seen == "198.51.100.7"


async def test_dependency_reads_repeated_forwarded_headers_as_one_list() -> None:
    headers = [("X-Forwarded-For", "203.0.113.5"), ("X-Forwarded-For", "2001:db8:1:2::9")]

    assert await _identity_seen(PROXY, headers, PROXY) == "2001:db8:1:2::/64"
