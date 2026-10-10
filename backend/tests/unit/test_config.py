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
