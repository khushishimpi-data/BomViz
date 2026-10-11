import ipaddress

import pytest
from pydantic import ValidationError

from tests.conftest import make_settings

STRONG_SECRET = "a" * 48


def test_cors_origins_parsed_from_comma_separated_value() -> None:
    settings = make_settings(cors_origins=" http://localhost:5173 , https://bomviz.example ,")

    assert settings.cors_origin_list == ["http://localhost:5173", "https://bomviz.example"]


def test_local_environment_allows_empty_jwt_secret() -> None:
    assert make_settings(app_env="local").jwt_secret_key.get_secret_value() == ""


@pytest.mark.parametrize("secret", ["", "change-me", "too-short"])
def test_production_rejects_weak_jwt_secret(secret: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        make_settings(app_env="production", jwt_secret_key=secret, refresh_cookie_secure=True)


def test_production_requires_secure_refresh_cookie() -> None:
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SECURE"):
        make_settings(app_env="production", jwt_secret_key=STRONG_SECRET)


def test_production_accepts_strong_configuration() -> None:
    settings = make_settings(
        app_env="production", jwt_secret_key=STRONG_SECRET, refresh_cookie_secure=True
    )

    assert settings.is_production


def test_auth_settings_have_secure_defaults() -> None:
    settings = make_settings()

    assert settings.jwt_issuer == "bomviz"
    assert settings.jwt_audience == "bomviz-api"
    assert settings.refresh_cookie_name == "bomviz_refresh"
    assert settings.refresh_cookie_path == "/api/v1/auth"
    assert settings.login_free_attempts == 4
    assert settings.login_base_delay_seconds == 30
    assert settings.login_max_delay_seconds == 900
    assert settings.login_throttle_reset_hours == 24
    assert settings.rate_limit_enabled is True


def test_rate_limiting_can_be_disabled_by_override() -> None:
    assert make_settings(rate_limit_enabled=False).rate_limit_enabled is False


def test_rate_limiting_can_be_disabled_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

    assert make_settings().rate_limit_enabled is False


def test_trusted_proxies_default_to_none() -> None:
    assert make_settings().trusted_proxy_networks == []


def test_trusted_proxies_parsed_from_comma_separated_ips_and_cidrs() -> None:
    settings = make_settings(trusted_proxy_ips=" 172.30.0.10 , 10.0.0.0/8 ,, fd00::/64 ")

    assert settings.trusted_proxy_networks == [
        ipaddress.ip_network("172.30.0.10/32"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("fd00::/64"),
    ]


@pytest.mark.parametrize("value", ["not-an-ip", "172.30.0.10, 300.1.1.1", "10.0.0.5/8", "*"])
def test_invalid_trusted_proxy_entry_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_IPS"):
        make_settings(trusted_proxy_ips=value)
