"""Provider-independent carrier tracking DTOs (DCSA Track & Trace inspired)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReferenceKind = Literal["container", "booking", "bill_of_lading"]
Classifier = Literal["PLANNED", "ESTIMATED", "ACTUAL"]
EventType = Literal["equipment", "transport", "shipment"]
Outcome = Literal["success", "failure", "rate_limited", "auth_error", "timeout"]
HealthStatus = Literal["connected", "authentication_required", "unavailable", "demo_data_only"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class TrackingReference(_Frozen):
    reference_type: ReferenceKind
    value: str = Field(min_length=3, max_length=64)


class CallRecord(_Frozen):
    """One provider call. Never contains request/response bodies (customer data)."""

    operation: str
    outcome: Outcome
    occurred_at: datetime
    http_status: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None
    provider_request_id: str | None = None


class ProviderResponse(BaseModel):
    reference: TrackingReference
    payload: Any
    fetched_at: datetime
    calls: list[CallRecord] = Field(default_factory=list)


class NormalizedEvent(_Frozen):
    event_type: EventType
    event_code: str  # DCSA code (LOAD, DISC, GTIN, GTOT, ARRI, DEPA, ...) or UNKNOWN
    classifier: Classifier
    event_at: datetime
    event_created_at: datetime | None = None
    location_unlocode: str | None = None
    location_name: str | None = None
    container_number: str | None = None
    vessel_imo: int | None = None
    vessel_name: str | None = None
    voyage_number: str | None = None
    transport_mode: str | None = None
    description: str | None = None
    raw_code: str | None = None
    source_event_id: str | None = None


class TrackingResult(BaseModel):
    reference: TrackingReference
    events: list[NormalizedEvent]
    fetched_at: datetime
    calls: list[CallRecord] = Field(default_factory=list)
    unmapped_codes: list[str] = Field(default_factory=list)
    skipped_events: int = 0


class IntegrationHealth(_Frozen):
    status: HealthStatus
    reason: str
    checked_at: datetime
    calls: tuple[CallRecord, ...] = ()


class CarrierError(Exception):
    """Base class; `code` is a stable machine-readable reason."""

    code = "carrier_error"

    def __init__(self, message: str, calls: list[CallRecord] | None = None) -> None:
        super().__init__(message)
        self.calls = calls or []


class ShipmentNotFound(CarrierError):
    code = "not_found"


class CarrierAuthError(CarrierError):
    code = "auth_error"


class CarrierRateLimited(CarrierError):
    code = "rate_limited"


class CarrierUnavailable(CarrierError):
    code = "unavailable"


class CarrierResponseInvalid(CarrierError):
    code = "invalid_response"
