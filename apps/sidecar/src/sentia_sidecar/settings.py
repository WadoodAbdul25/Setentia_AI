from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SENTIA_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=43120, ge=1, le=65535)
    token: SecretStr = Field(min_length=32)
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./.sentia/sentia.db"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    codex_model: str | None = None

    @field_validator("host")
    @classmethod
    def require_loopback(cls, value: str) -> str:
        try:
            address = ip_address(value)
        except ValueError as error:
            raise ValueError("SENTIA_HOST must be a loopback IP address") from error
        if not address.is_loopback:
            raise ValueError("Sentia sidecar may bind only to a loopback address")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
