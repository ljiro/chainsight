"""Shipment tracking API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from chainsight_api.schemas.fleet import LatestPosition
from database.enums import DataMode, DelayStatus, ReferenceType, ShipmentStatus


class CarrierRef(BaseModel):
    code: str
    name: str


class Location(BaseModel):
    unlocode: str | None
    port_name: str | None
    # False when the UN/LOCODE has no coordinates in the registry (cannot be mapped).
    mapped: bool


class ShipmentProvenance(BaseModel):
    source: str
    data_mode: DataMode


class ShipmentSummary(BaseModel):
    id: uuid.UUID
    carrier: CarrierRef
    reference_type: ReferenceType
    reference_value: str
    origin: Location
    destination: Location
    status: ShipmentStatus
    planned_arrival_at: datetime | None
    current_eta_at: datetime | None
    actual_arrival_at: datetime | None
    delay_hours: float | None
    delay_status: DelayStatus
    last_event_at: datetime | None
    last_refreshed_at: datetime | None
    containers: list[str]
    provenance: ShipmentProvenance


class ShipmentEventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    event_code: str
    classifier: Literal["PLANNED", "ESTIMATED", "ACTUAL"]
    event_at: datetime
    event_created_at: datetime | None
    location_unlocode: str | None
    location_name: str | None
    container_number: str | None
    vessel_name: str | None
    vessel_imo: int | None
    voyage_number: str | None
    description: str | None
    raw_code: str | None
    ingested_at: datetime


class VesselLeg(BaseModel):
    sequence: int
    carrier_vessel_name: str | None
    carrier_vessel_imo: int | None
    carrier_voyage_number: str | None
    load_unlocode: str | None
    discharge_unlocode: str | None
    # Set only when the carrier-reported IMO matches a tracked vessel.
    vessel_id: uuid.UUID | None
    vessel_position: LatestPosition | None


class ShipmentDetail(ShipmentSummary):
    events: list[ShipmentEventOut]
    legs: list[VesselLeg]
    integration_status: str | None
    refreshable: bool


class TrackRequest(BaseModel):
    carrier: str = Field(min_length=2, max_length=32)
    reference: str = Field(min_length=3, max_length=64)
    reference_type: ReferenceType | None = None


class ShipmentsSummary(BaseModel):
    total: int
    active: int
    delayed: int
    arrived_late: int
    by_status: dict[str, int]
    by_delay_status: dict[str, int]
    by_data_mode: dict[str, int]


class ImportError_(BaseModel):
    row: int
    field: str
    message: str


class ImportReportOut(BaseModel):
    ok: bool
    rows: int
    shipments_created: int
    shipments_updated: int
    events_inserted: int
    events_duplicate: int
    errors: list[ImportError_]


class CarrierPerformance(BaseModel):
    code: str
    name: str
    carrier_type: str
    integration_status: str | None
    integration_reason: str | None
    last_call_at: datetime | None
    last_call_outcome: str | None
    vessels_associated: int
    shipments_tracked: int
    shipments_active: int
    shipments_delayed: int
    arrivals: int
    on_time_rate: float | None = Field(description="Arrived within threshold / arrived")
    avg_arrival_delay_hours: float | None
