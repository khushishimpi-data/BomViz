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


def test_upload_and_parse_limits_have_documented_defaults() -> None:
    settings = make_settings()

    assert settings.max_upload_bytes == 20 * 1024 * 1024
    assert settings.parse_max_rows == 50_000
    assert settings.parse_max_columns == 200
    assert settings.parse_max_cell_chars == 32_767
    assert settings.parse_sample_rows == 100
    assert settings.parse_max_uncompressed_bytes == 200 * 1024 * 1024
    assert settings.job_stale_after_minutes == 15


def test_upload_and_parse_limits_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("PARSE_MAX_ROWS", "10")
    monkeypatch.setenv("PARSE_MAX_COLUMNS", "5")
    monkeypatch.setenv("PARSE_MAX_CELL_CHARS", "50")
    monkeypatch.setenv("PARSE_SAMPLE_ROWS", "3")
    monkeypatch.setenv("PARSE_MAX_UNCOMPRESSED_BYTES", "4096")
    monkeypatch.setenv("JOB_STALE_AFTER_MINUTES", "1")
    monkeypatch.setenv("PARSE_SOFT_TIME_LIMIT_SECONDS", "30")
    monkeypatch.setenv("PARSE_HARD_TIME_LIMIT_SECONDS", "45")

    settings = make_settings()

    assert settings.max_upload_bytes == 1024
    assert settings.parse_max_rows == 10
    assert settings.parse_max_columns == 5
    assert settings.parse_max_cell_chars == 50
    assert settings.parse_sample_rows == 3
    assert settings.parse_max_uncompressed_bytes == 4096
    assert settings.job_stale_after_minutes == 1
    assert settings.parse_soft_time_limit_seconds == 30
    assert settings.parse_hard_time_limit_seconds == 45


def test_parse_time_limits_default_to_ten_and_eleven_minutes() -> None:
    settings = make_settings()

    assert settings.parse_soft_time_limit_seconds == 600
    assert settings.parse_hard_time_limit_seconds == 660


def test_a_soft_parse_time_limit_reaching_the_stale_job_age_is_rejected() -> None:
    with pytest.raises(ValidationError, match="JOB_STALE_AFTER_MINUTES"):
        make_settings(job_stale_after_minutes=10, parse_soft_time_limit_seconds=600)


def test_a_hard_parse_time_limit_not_above_the_soft_limit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PARSE_HARD_TIME_LIMIT_SECONDS"):
        make_settings(parse_soft_time_limit_seconds=600, parse_hard_time_limit_seconds=600)


def test_a_non_positive_soft_parse_time_limit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PARSE_SOFT_TIME_LIMIT_SECONDS"):
        make_settings(parse_soft_time_limit_seconds=0)


def test_non_integer_upload_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "20MB")

    with pytest.raises(ValidationError, match="max_upload_bytes"):
        make_settings()
