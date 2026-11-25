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
