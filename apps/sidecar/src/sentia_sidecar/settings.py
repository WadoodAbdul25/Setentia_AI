from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SENTIA_",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=43120, ge=1, le=65535)
    token: SecretStr = Field(min_length=32)
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./.sentia/sentia.db"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    codex_model: str | None = None
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("SENTIA_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    cerebras_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("SENTIA_CEREBRAS_API_KEY", "CEREBRAS_API_KEY")
    )
    cerebras_url: str = Field(
        default="https://api.cerebras.ai/v1",
        validation_alias=AliasChoices("SENTIA_CEREBRAS_URL", "CEREBRAS_URL"),
    )
    cerebras_model: str = "gpt-oss-120b"
    voice_progress_phrases: bool = True
    openai_eot_enabled: bool = True
    openai_eot_model_id: str = "HuggingFaceTB/SmolLM2-360M-Instruct"
    openai_eot_threshold: float = Field(default=0.03, ge=0, le=1, allow_inf_nan=False)
    openai_eot_min_silence_ms: int = Field(default=600, ge=100, le=60_000)
    openai_eot_inference_timeout_ms: int = Field(default=350, ge=1, le=60_000)
    openai_eot_max_input_tokens: int = Field(default=2048, ge=1, le=8192)

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
    # The development sidecar launches from the repository root. Read its local
    # credentials without copying them into process-wide environment variables.
    return Settings(_env_file=(".env", "apps/sidecar/.env"))
