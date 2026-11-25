"""Typed application settings loaded from environment variables (and `.env` in development).

Provider credentials are `SecretStr` so they never appear in logs, reprs, or API responses.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AisProvider(StrEnum):
    DISABLED = "disabled"
    AISSTREAM = "aisstream"
    REPLAY = "replay"


# Set CHAINSIGHT_ENV_FILE="" to ignore `.env` entirely (the test suite does this).
_ENV_FILE = os.getenv("CHAINSIGHT_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ChainSight"
    app_version: str = "0.1.0"
    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: str = "INFO"
    log_format: str = Field(default="console", pattern="^(console|json)$")
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://chainsight:chainsight@localhost:5432/chainsight"
    )
    database_pool_size: int = 5
    database_echo: bool = False

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Integrations (Phase 2+). Empty means "not configured" -> reported honestly in the UI.
    ais_provider: AisProvider = AisProvider.DISABLED
    aisstream_api_key: SecretStr | None = None
    ais_replay_file: str | None = None
    open_meteo_api_key: SecretStr | None = None

    maersk_consumer_key: SecretStr | None = None
    maersk_consumer_secret: SecretStr | None = None
    hapag_lloyd_client_id: SecretStr | None = None
    hapag_lloyd_client_secret: SecretStr | None = None
    cma_cgm_api_key: SecretStr | None = None
    dhl_api_key: SecretStr | None = None

    un_comtrade_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    eia_api_key: SecretStr | None = None

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator(
        "aisstream_api_key",
        "open_meteo_api_key",
        "maersk_consumer_key",
        "maersk_consumer_secret",
        "hapag_lloyd_client_id",
        "hapag_lloyd_client_secret",
        "cma_cgm_api_key",
        "dhl_api_key",
        "un_comtrade_api_key",
        "fred_api_key",
        "eia_api_key",
        mode="before",
    )
    @classmethod
    def _blank_secret_is_none(cls, value: object) -> object:
        # `KEY=` in .env should mean "not configured", not "configured with an empty key".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def sync_database_url(self) -> str:
        return str(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
