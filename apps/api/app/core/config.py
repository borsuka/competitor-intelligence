"""Application configuration.

All configuration comes from environment variables (or a local ``.env``).  Nothing is
hardcoded and nothing secret has a usable default: :meth:`Settings.validate_production`
refuses to boot in production if a development placeholder survived.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# Placeholders that must never reach production.  Kept as a set so the check is exact
# rather than a substring guess.
INSECURE_DEFAULTS = {
    "change-me",
    "dev-insecure-secret-change-me",
    "secret",
    "",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    environment: Environment = "development"
    debug: bool = False
    app_name: str = "Sentinel"
    api_v1_prefix: str = "/api/v1"
    public_web_url: str = "http://localhost:3000"

    # ------------------------------------------------------------- security
    secret_key: str = "dev-insecure-secret-change-me"
    password_pepper: SecretStr = SecretStr("")
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 30 * 24 * 60 * 60
    verification_token_ttl_seconds: int = 24 * 60 * 60
    password_reset_token_ttl_seconds: int = 60 * 60

    cookie_domain: str | None = None
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Comma separated.  Parsed by :attr:`cors_origin_list` — kept as a plain string
    # because pydantic-settings would otherwise demand JSON syntax in the env file.
    cors_origins: str = "http://localhost:3000"

    # ------------------------------------------------------------- database
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # ------------------------------------------------------------------- ai
    ai_provider: Literal["anthropic", "mock"] = "mock"
    anthropic_api_key: SecretStr | None = None
    ai_model_extraction: str = "claude-haiku-4-5-20251001"
    ai_model_synthesis: str = "claude-sonnet-5"
    ai_max_output_tokens: int = 4096
    ai_timeout_seconds: float = 120.0
    ai_max_input_chars: int = 60_000
    embedding_dimensions: int = 1024
    embedding_model: str = "voyage-3"
    # Optional. Without it the app falls back to a lexical hashing embedder, which
    # is real but not semantic — flagged as such everywhere results are shown.
    voyage_api_key: SecretStr | None = None

    # ------------------------------------------------------------- scraping
    scraper_user_agent: str = (
        "SentinelBot/0.1 (+https://github.com/borsuka/competitor-intelligence; "
        "competitor intelligence crawler)"
    )
    scraper_enable_js: bool = False
    scraper_max_pages: int = 25
    scraper_timeout_seconds: float = 15.0
    scraper_max_bytes: int = 2_000_000
    scraper_max_redirects: int = 5
    scraper_respect_robots: bool = True
    scraper_delay_seconds: float = 1.0
    # Escape hatch for tests that point the fetcher at a local server.  Refused in
    # production by :meth:`validate_production` — this flag disables the SSRF guard.
    scraper_allow_private_networks: bool = False

    # --------------------------------------------------------------- quotas
    quota_competitors: int = 25
    quota_analyses_per_month: int = 200
    quota_pages_per_month: int = 5_000
    quota_ai_tokens_per_month: int = 5_000_000

    # --------------------------------------------------------- rate limiting
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10
    rate_limit_write_per_minute: int = 60
    rate_limit_analysis_per_hour: int = 30
    rate_limit_read_per_minute: int = 300

    # -------------------------------------------------------- observability
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    sentry_dsn: SecretStr | None = None
    otel_exporter_otlp_endpoint: str | None = None

    # ------------------------------------------------------------ monitoring
    monitoring_default_interval_hours: int = 24
    monitoring_batch_size: int = 50

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str = "no-reply@sentinel.local"

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        """Fail fast rather than run production on development defaults."""
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.secret_key in INSECURE_DEFAULTS or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.debug:
            problems.append("DEBUG must be false in production")
        if self.scraper_allow_private_networks:
            problems.append(
                "SCRAPER_ALLOW_PRIVATE_NETWORKS must be false in production "
                "(it disables the SSRF guard)"
            )
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is required when AI_PROVIDER=anthropic")
        if "localhost" in self.database_url:
            problems.append("DATABASE_URL still points at localhost")
        if problems:
            raise ValueError(
                "Refusing to start in production:\n  - " + "\n  - ".join(problems)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that config is read once at boot; tests clear the cache via
    ``get_settings.cache_clear()``.
    """
    return Settings()


def generate_secret() -> str:
    """Helper used by ``scripts/`` and tests to mint a usable SECRET_KEY."""
    return secrets.token_urlsafe(48)


__all__ = ["Settings", "get_settings", "generate_secret", "Environment"]
