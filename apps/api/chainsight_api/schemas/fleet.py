"""Fleet, vessel, port, and alert response schemas.

Every position carries provenance: the source key, its data mode (real_time / historical /
synthetic) and its age, so clients can never present stale or synthetic data as live.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from chainsight_api.schemas.common import ApiModel
from database.enums import DataMode, VesselClass

PositionFreshness = Literal["live", "recent", "stale"]


class Provenance(BaseModel):
    source: str | None
    data_mode: DataMode | None


class LatestPosition(BaseModel):
    lat: float
    lon: float
    sog_knots: float | None
    cog_deg: float | None
    heading_deg: int | None
    nav_status: int | None
    nav_status_label: str | None
    observed_at: datetime
    age_seconds: float
    freshness: PositionFreshness
    provenance: Provenance


class CarrierAssociation(ApiModel):
    carrier_code: str
    carrier_name: str
    relation: str
    method: str
    confidence: float
    valid_from: datetime


class VesselSummary(BaseModel):
    id: uuid.UUID
    mmsi: int | None
    imo: int | None
    name: str | None
    vessel_class: VesselClass
    vessel_class_source: str | None
    destination: str | None
    carrier: CarrierAssociation | None
    position: LatestPosition | None


class VesselIdentifierOut(ApiModel):
    scheme: str
    value: str
    valid_from: datetime
    valid_to: datetime | None
    source: str | None


class VesselDetail(VesselSummary):
    call_sign: str | None
    ship_type_code: int | None
    length_m: int | None
    beam_m: int | None
    draught_m: float | None
    first_seen_at: datetime | None
    identifiers: list[VesselIdentifierOut]
    carrier_history: list[CarrierAssociation]
    open_port_call: PortCallOut | None


class TrackGap(BaseModel):
    start: datetime
    end: datetime
    duration_seconds: float


class TrackResponse(BaseModel):
    vessel_id: uuid.UUID
    start: datetime
    end: datetime
    total_points: int
    returned_points: int
    # GeoJSON MultiLineString: one line per continuous segment (split at AIS gaps).
    geometry: dict[str, Any]
    points: list[TrackPoint]
    gaps: list[TrackGap]
    gap_threshold_seconds: int
    sources: list[Provenance]


class TrackPoint(BaseModel):
    t: datetime
    lat: float
    lon: float
    sog_knots: float | None
    cog_deg: float | None
    quality_flags: int


class PortRef(BaseModel):
    id: uuid.UUID
    unlocode: str | None
    name: str
    country_code: str


class PortCallOut(BaseModel):
    id: uuid.UUID
    port: PortRef
    vessel_id: uuid.UUID
    vessel_name: str | None = None
    # None means "not observed": e.g. the vessel was already in port when tracking began.
    actual_arrival_at: datetime | None
    actual_departure_at: datetime | None
    duration_seconds: float | None
    detection_method: str
    source: str | None


class PortOut(BaseModel):
    id: uuid.UUID
    unlocode: str | None
    name: str
    country_code: str
    lat: float
    lon: float
    geofence_radius_m: int | None
    vessels_in_port: int


class WeatherOut(BaseModel):
    valid_at: datetime
    retrieved_at: datetime
    temperature_c: float | None
    wind_speed_kn: float | None
    wind_direction_deg: float | None
    wind_gusts_kn: float | None
    precipitation_mm: float | None
    source: str
    attribution: str | None


class PortDetail(PortOut):
    source: str | None
    weather: WeatherOut | None
    in_port: list[PortCallOut]
    recent_calls: list[PortCallOut]


class FleetSummary(BaseModel):
    generated_at: datetime
    vessels_total: int
    vessels_live: int = Field(description="Position within the last 10 minutes")
    vessels_recent: int = Field(description="Position within the last hour")
    underway: int
    at_anchor_or_moored: int
    in_port: int
    by_class: dict[str, int]
    by_data_mode: dict[str, int]
    carrier_associations: dict[str, int]
    port_calls_last_24h: int


class ApproachingVessel(BaseModel):
    vessel: VesselSummary
    port: PortRef
    distance_nm: float


class AlertOut(ApiModel):
    id: uuid.UUID
    severity: str
    category: str
    title: str
    message: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    data_source: str | None
    raised_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None


VesselDetail.model_rebuild()
TrackResponse.model_rebuild()
