"""ORM models. Importing this package registers every table on `Base.metadata`."""

from database.models.carriers import Carrier, CarrierIntegration, CarrierIntegrationLog
from database.models.fleet import (
    Vessel,
    VesselCarrierHistory,
    VesselIdentifier,
    VesselPosition,
    VesselPositionSource,
    Voyage,
    VoyagePortCall,
)
from database.models.geo import Port
from database.models.identity import ApiKey, Organization, OrganizationMember, User
from database.models.ops import AuditLog, SystemAlert
from database.models.port_activity import (
    MaritimeRoute,
    PortArea,
    PortObservation,
    WeatherObservation,
)
from database.models.shipments import (
    Container,
    Shipment,
    ShipmentEvent,
    ShipmentItem,
    ShipmentVessel,
)
from database.models.sources import DataSource, IngestionLog

__all__ = [
    "ApiKey",
    "AuditLog",
    "Carrier",
    "CarrierIntegration",
    "CarrierIntegrationLog",
    "Container",
    "DataSource",
    "IngestionLog",
    "MaritimeRoute",
    "Organization",
    "OrganizationMember",
    "Port",
    "PortArea",
    "PortObservation",
    "Shipment",
    "ShipmentEvent",
    "ShipmentItem",
    "ShipmentVessel",
    "SystemAlert",
    "User",
    "Vessel",
    "VesselCarrierHistory",
    "VesselIdentifier",
    "VesselPosition",
    "VesselPositionSource",
    "Voyage",
    "VoyagePortCall",
    "WeatherObservation",
]
