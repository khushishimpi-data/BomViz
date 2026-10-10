"""Application settings loaded from environment variables (and `.env` in local development)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PRODUCTION_SECRET_LENGTH = 32
_PLACEHOLDER_SECRETS = frozenset({"", "change-me", "changeme", "secret"})


class Settings(BaseSettings):
    """Typed runtime configuration. Every value can be overridden by an environment variable."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    app_name: str = "BOMViz"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = ""

    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 10

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_always_eager: bool = False

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    refresh_cookie_secure: bool = False

    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_bucket: str = "bomviz"
    s3_region: str = "us-east-1"
    signed_url_ttl_seconds: int = 300

    readiness_timeout_seconds: float = 2.0

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed from the comma-separated `CORS_ORIGINS` value."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def _require_strong_secret_in_production(self) -> Self:
        if not self.is_production:
            return self
        secret = self.jwt_secret_key.get_secret_value()
        if secret.lower() in _PLACEHOLDER_SECRETS or len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a random value of at least "
                f"{MIN_PRODUCTION_SECRET_LENGTH} characters in production"
            )
        if self.refresh_cookie_secure is False:
            raise ValueError("REFRESH_COOKIE_SECURE must be true in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
