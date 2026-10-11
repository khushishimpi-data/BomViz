"""Application settings loaded from environment variables (and `.env` in local development)."""

import ipaddress
from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.client_ip import IPNetwork

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
    jwt_issuer: str = "bomviz"
    jwt_audience: str = "bomviz-api"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    refresh_cookie_name: str = "bomviz_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    refresh_cookie_secure: bool = False

    login_free_attempts: int = 4
    login_base_delay_seconds: int = 30
    login_max_delay_seconds: int = 900
    login_throttle_reset_hours: int = 24
    rate_limit_enabled: bool = True
    # Comma-separated IPs/CIDRs of reverse proxies whose X-Forwarded-For header is believed.
    # Empty (the default) trusts nobody: the client IP is always the direct peer address.
    trusted_proxy_ips: str = ""

    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_bucket: str = "bomviz"
    s3_region: str = "us-east-1"
    signed_url_ttl_seconds: int = 300

    # BOM upload and parsing limits (Phase 2).
    max_upload_bytes: int = 20 * 1024 * 1024
    parse_max_rows: int = 50_000
    parse_max_columns: int = 200
    parse_max_cell_chars: int = 32_767
    parse_sample_rows: int = 100
    parse_max_uncompressed_bytes: int = 200 * 1024 * 1024
    job_stale_after_minutes: int = 15
    # Celery limits on one `boms.parse` run: the soft limit fails the job with
    # `PARSE_FILE_TOO_COMPLEX`; the hard limit kills the worker process.
    parse_soft_time_limit_seconds: int = 600
    parse_hard_time_limit_seconds: int = 660

    readiness_timeout_seconds: float = 2.0

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed from the comma-separated `CORS_ORIGINS` value."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_proxy_networks(self) -> list[IPNetwork]:
        """`TRUSTED_PROXY_IPS` parsed into networks (a bare IP becomes a /32 or /128)."""
        return _parse_networks(self.trusted_proxy_ips)

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _validate_trusted_proxy_ips(cls, value: str) -> str:
        _parse_networks(value)
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @model_validator(mode="after")
    def _check_parse_time_limits(self) -> Self:
        # A running job is only treated as stale (and replaceable) after the soft limit has had
        # the chance to fail it, and the hard limit must leave time to record that failure.
        soft = self.parse_soft_time_limit_seconds
        if soft <= 0:
            raise ValueError("PARSE_SOFT_TIME_LIMIT_SECONDS must be positive")
        if self.parse_hard_time_limit_seconds <= soft:
            raise ValueError(
                "PARSE_HARD_TIME_LIMIT_SECONDS must be greater than PARSE_SOFT_TIME_LIMIT_SECONDS"
            )
        if soft >= self.job_stale_after_minutes * 60:
            raise ValueError(
                "PARSE_SOFT_TIME_LIMIT_SECONDS must be less than JOB_STALE_AFTER_MINUTES * 60"
            )
        return self

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


def _parse_networks(value: str) -> list[IPNetwork]:
    networks: list[IPNetwork] = []
    for entry in (part.strip() for part in value.split(",")):
        if not entry:
            continue
        try:
            # strict: a CIDR with host bits set (e.g. 10.0.0.5/24) is a likely typo, not a range.
            networks.append(ipaddress.ip_network(entry, strict=True))
        except ValueError as exc:
            raise ValueError(
                f"TRUSTED_PROXY_IPS entry {entry!r} is not an IP address or CIDR"
            ) from exc
    return networks


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
