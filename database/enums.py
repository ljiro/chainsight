"""Enumerations persisted in the database. Values are stable identifiers; do not rename."""

from enum import StrEnum


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class DataSourceCategory(StrEnum):
    AIS = "ais"
    WEATHER = "weather"
    PORT = "port"
    CARRIER = "carrier"
    TRADE = "trade"
    MARKET = "market"
    INTERNAL = "internal"


class DataMode(StrEnum):
    """How the data relates to reality. Always surfaced in the UI."""

    REAL_TIME = "real_time"
    PERIODIC = "periodic"
    HISTORICAL = "historical"
    SYNTHETIC = "synthetic"


class DataSourceStatus(StrEnum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class CarrierType(StrEnum):
    OCEAN_CARRIER = "ocean_carrier"
    LOGISTICS_PROVIDER = "logistics_provider"


class IntegrationCapability(StrEnum):
    SHIPMENT_TRACKING = "shipment_tracking"
    VESSEL_SCHEDULES = "vessel_schedules"
    FLEET_LIST = "fleet_list"


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PENDING_APPROVAL = "pending_approval"
    UNAVAILABLE = "unavailable"
    DEMO_DATA_ONLY = "demo_data_only"


class IntegrationOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    RATE_LIMITED = "rate_limited"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class VesselClass(StrEnum):
    """Coarse class. AIS ship types cannot distinguish container/bulk/LNG; those values are
    reserved for registry enrichment and must never be inferred from AIS alone."""

    CARGO = "cargo"
    TANKER = "tanker"
    PASSENGER = "passenger"
    FISHING = "fishing"
    TUG_SERVICE = "tug_service"
    PLEASURE = "pleasure"
    HIGH_SPEED = "high_speed"
    OTHER = "other"
    UNKNOWN = "unknown"
    CONTAINER = "container"
    BULK = "bulk"
    LNG_CARRIER = "lng_carrier"
    LPG_CARRIER = "lpg_carrier"


class VesselIdentifierScheme(StrEnum):
    MMSI = "mmsi"
    IMO = "imo"
    CALL_SIGN = "call_sign"
    NAME = "name"


class CarrierRelation(StrEnum):
    OPERATOR = "operator"
    OWNER = "owner"
    CHARTERER = "charterer"
    SLOT = "slot"


class AssociationMethod(StrEnum):
    NAME_HEURISTIC = "name_heuristic"
    MANUAL = "manual"
    CARRIER_API = "carrier_api"


class VoyageStatus(StrEnum):
    PLANNED = "planned"
    UNDERWAY = "underway"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PortCallDetection(StrEnum):
    CARRIER_SCHEDULE = "carrier_schedule"
    AIS_GEOFENCE = "ais_geofence"


class WeatherKind(StrEnum):
    CURRENT = "current"
    FORECAST = "forecast"
    HISTORICAL = "historical"


class ReferenceType(StrEnum):
    CONTAINER = "container"
    BOOKING = "booking"
    BILL_OF_LADING = "bill_of_lading"


class ShipmentStatus(StrEnum):
    """Derived from ACTUAL events only; never from estimates."""

    UNKNOWN = "unknown"
    PLANNED = "planned"
    AT_ORIGIN = "at_origin"
    LOADED = "loaded"
    IN_TRANSIT = "in_transit"
    TRANSSHIPMENT = "transshipment"
    DISCHARGED = "discharged"
    DELIVERED = "delivered"


class DelayStatus(StrEnum):
    UNKNOWN = "unknown"
    ON_TIME = "on_time"
    DELAYED = "delayed"
    ARRIVED_ON_TIME = "arrived_on_time"
    ARRIVED_LATE = "arrived_late"


class ShipmentEventType(StrEnum):
    EQUIPMENT = "equipment"
    TRANSPORT = "transport"
    SHIPMENT = "shipment"


class EventClassifier(StrEnum):
    """DCSA eventClassifierCode: PLN / EST / ACT."""

    PLANNED = "PLANNED"
    ESTIMATED = "ESTIMATED"
    ACTUAL = "ACTUAL"
