"""Response schemas for reference/catalog resources (data sources, carriers)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from chainsight_api.schemas.common import ApiModel
from database.enums import (
    CarrierType,
    DataMode,
    DataSourceCategory,
    DataSourceStatus,
    IntegrationCapability,
    IntegrationStatus,
)

Freshness = Literal["fresh", "stale", "never", "unknown"]


class DataSourceOut(ApiModel):
    key: str
    name: str
    category: DataSourceCategory
    data_mode: DataMode
    status: DataSourceStatus
    homepage_url: str | None
    license_note: str | None
    attribution: str | None
    expected_update_interval_seconds: int | None
    last_success_at: datetime | None
    last_error_at: datetime | None
    freshness: Freshness


class CarrierIntegrationOut(ApiModel):
    capability: IntegrationCapability
    status: IntegrationStatus
    status_reason: str | None
    last_checked_at: datetime | None
    last_success_at: datetime | None


class CarrierOut(ApiModel):
    code: str
    name: str
    scac: str | None
    carrier_type: CarrierType
    website_url: str | None
    integrations: list[CarrierIntegrationOut]
