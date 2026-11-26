"""Typed application settings loaded from environment variables (and `.env` in development).

Provider credentials are `SecretStr` so they never appear in logs, reprs, or API responses.
"""

from __future__ import annotations

import json
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
    SIMULATOR = "simulator"


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
    # JSON list of boxes [[[lat1, lon1], [lat2, lon2]], ...]; default: whole world.
    ais_bounding_boxes: Annotated[list[list[list[float]]], NoDecode] = Field(
        default_factory=lambda: [[[-90.0, -180.0], [90.0, 180.0]]]
    )
    ais_replay_file: str | None = None
    ais_replay_speed: float = Field(default=1.0, ge=0)
    ais_record_file: str | None = None
    ais_simulator_vessels: int = Field(default=24, ge=1, le=24)
    ais_simulator_interval_seconds: float = Field(default=30.0, gt=0)
    ais_batch_size: int = Field(default=500, ge=1)
    ais_flush_interval_seconds: float = Field(default=1.0, gt=0)

    port_geofence_radius_m: int = Field(default=8000, ge=500, le=50_000)
    un_locode_url: str = (
        "https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv"
    )

    open_meteo_api_key: SecretStr | None = None
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_port_unlocodes: Annotated[list[str], NoDecode] = Field(default_factory=list)
    weather_max_ports: int = Field(default=50, ge=1, le=500)

    # Shipment tracking (Phase 3). JSON object: {"<carrier code>": {"base_url": ..., "auth":
    # {"type": "api_key"|"bearer"|"oauth2_client_credentials", ...}}}. See
    # docs/integrations/carriers.md. Held as a secret because it contains credentials.
    carrier_dcsa_config: SecretStr | None = None
    shipment_delay_threshold_hours: float = Field(default=12.0, ge=0)
    shipment_auto_refresh_minutes: int = Field(default=60, ge=5)

    # Machine learning (Phase 4)
    mlflow_tracking_uri: str | None = "sqlite:///./data/mlflow.db"
    model_artifact_dir: str = "./data/models"
    eta_min_training_voyages: int = Field(default=50, ge=1)
    delay_min_training_shipments: int = Field(default=200, ge=1)
    delay_alert_probability: float = Field(default=0.7, ge=0, le=1)

    maersk_consumer_key: SecretStr | None = None
    maersk_consumer_secret: SecretStr | None = None
    hapag_lloyd_client_id: SecretStr | None = None
    hapag_lloyd_client_secret: SecretStr | None = None
    cma_cgm_api_key: SecretStr | None = None
    dhl_api_key: SecretStr | None = None

    un_comtrade_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    eia_api_key: SecretStr | None = None

    @field_validator("api_cors_origins", "weather_port_unlocodes", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("ais_bounding_boxes", mode="before")
    @classmethod
    def _parse_boxes(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else [[[-90.0, -180.0], [90.0, 180.0]]]
        return value

    @field_validator(
        "aisstream_api_key",
        "open_meteo_api_key",
        "carrier_dcsa_config",
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
