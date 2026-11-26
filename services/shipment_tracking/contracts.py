"""The service-layer surface the API uses for carrier tracking.

The API must not import `integrations` directly (system-design.md §3.1), so the carrier
error types and reference helpers it needs are re-exported here.
"""

from integrations.carriers.models import (
    CarrierAuthError,
    CarrierError,
    CarrierRateLimited,
    ShipmentNotFound,
    TrackingReference,
)
from integrations.carriers.references import detect_reference_type, normalize_reference

__all__ = [
    "CarrierAuthError",
    "CarrierError",
    "CarrierRateLimited",
    "ShipmentNotFound",
    "TrackingReference",
    "detect_reference_type",
    "normalize_reference",
]
