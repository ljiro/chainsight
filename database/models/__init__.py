"""ORM models. Importing this package registers every table on `Base.metadata`."""

from database.models.carriers import (
    Carrier,
    CarrierIntegration,
    CarrierIntegrationLog,
    WebhookDelivery,
)
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
from database.models.identity import ApiKey, AuthSession, Organization, OrganizationMember, User
from database.models.ml import (
    CongestionPrediction,
    DelayPrediction,
    EtaPrediction,
    ModelEvaluation,
    ModelVersion,
    RiskAssessment,
)
from database.models.ops import AuditLog, SystemAlert
from database.models.port_activity import (
    MaritimeRoute,
    PortArea,
    PortObservation,
    WeatherObservation,
)
from database.models.research import (
    BacktestMetric,
    BacktestRun,
    IndicatorObservation,
    IndicatorSeries,
    MaritimeFeature,
    ResearchDataset,
    ResearchExperiment,
    ResearchSignal,
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
    "AuthSession",
    "BacktestMetric",
    "BacktestRun",
    "Carrier",
    "CarrierIntegration",
    "CarrierIntegrationLog",
    "CongestionPrediction",
    "Container",
    "DataSource",
    "DelayPrediction",
    "EtaPrediction",
    "IndicatorObservation",
    "IndicatorSeries",
    "IngestionLog",
    "MaritimeFeature",
    "MaritimeRoute",
    "ModelEvaluation",
    "ModelVersion",
    "Organization",
    "OrganizationMember",
    "Port",
    "PortArea",
    "PortObservation",
    "ResearchDataset",
    "ResearchExperiment",
    "ResearchSignal",
    "RiskAssessment",
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
    "WebhookDelivery",
]
