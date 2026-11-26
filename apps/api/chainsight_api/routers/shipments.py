"""Shipment tracking and carrier performance endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from chainsight_api.errors import ApiError
from chainsight_api.schemas.common import Page, PageParams
from chainsight_api.schemas.shipments import (
    CarrierPerformance,
    ImportReportOut,
    ShipmentDetail,
    ShipmentsSummary,
    ShipmentSummary,
    TrackRequest,
)
from core.settings import get_settings
from core.timeutils import utcnow
from database.enums import DelayStatus, ShipmentStatus
from database.session import session_scope
from services.shipment_tracking import queries
from services.shipment_tracking.contracts import (
    CarrierAuthError,
    CarrierError,
    CarrierRateLimited,
    ShipmentNotFound,
    TrackingReference,
    detect_reference_type,
    normalize_reference,
)
from services.shipment_tracking.csv_import import import_csv
from services.shipment_tracking.registry import CarrierRegistry, build_registry
from services.shipment_tracking.service import (
    CarrierNotFound,
    IntegrationNotAvailable,
    local_org_id,
    track,
)

router = APIRouter(tags=["shipments"])
Session = Annotated[AsyncSession, Depends(session_scope)]
MAX_CSV_BYTES = 5 * 1024 * 1024


def get_registry(request: Request) -> CarrierRegistry:
    registry = getattr(request.app.state, "carrier_registry", None)
    if registry is None:
        registry = build_registry(get_settings())
        request.app.state.carrier_registry = registry
    return registry


Registry = Annotated[CarrierRegistry, Depends(get_registry)]


async def _detail_or_404(session: AsyncSession, shipment_id: uuid.UUID) -> dict[str, Any]:
    detail = await queries.get_shipment(session, await local_org_id(session), shipment_id, utcnow())
    if detail is None:
        raise ApiError(404, "shipment_not_found", "Shipment not found")
    return detail


async def _track(
    session: AsyncSession, registry: CarrierRegistry, carrier: str, ref: TrackingReference
) -> uuid.UUID:
    threshold = get_settings().shipment_delay_threshold_hours
    try:
        outcome = await track(
            session, registry, carrier_code=carrier, reference=ref, threshold_hours=threshold
        )
    except CarrierNotFound as exc:
        raise ApiError(404, "carrier_not_found", f"Unknown carrier '{carrier}'") from exc
    except IntegrationNotAvailable as exc:
        raise ApiError(
            409,
            "carrier_integration_unavailable",
            f"Tracking with this carrier is not available ({exc.status.replace('_', ' ')}).",
            {"status": exc.status, "reason": exc.reason},
        ) from exc
    except ShipmentNotFound as exc:
        raise ApiError(404, "reference_not_found", str(exc)) from exc
    except CarrierAuthError as exc:
        raise ApiError(
            502, "carrier_auth_failed", "The carrier rejected the configured credentials."
        ) from exc
    except CarrierRateLimited as exc:
        raise ApiError(
            503, "carrier_rate_limited", "The carrier is rate limiting requests; retry later."
        ) from exc
    except CarrierError as exc:
        raise ApiError(503, "carrier_unavailable", str(exc)) from exc
    await session.commit()
    return outcome.shipment_id


@router.get("/shipments", response_model=Page[ShipmentSummary], summary="Search tracked shipments")
async def list_shipments(
    session: Session,
    page: Annotated[PageParams, Query()],
    q: Annotated[str | None, Query(min_length=2, max_length=64)] = None,
    carrier: Annotated[str | None, Query(max_length=32)] = None,
    status: ShipmentStatus | None = None,
    delay_status: DelayStatus | None = None,
    active_only: bool = False,
) -> Page[ShipmentSummary]:
    items, total = await queries.list_shipments(
        session, await local_org_id(session), q=q, carrier=carrier,
        status=status.value if status else None,
        delay_status=delay_status.value if delay_status else None,
        active_only=active_only, limit=page.limit, offset=page.offset,
    )  # fmt: skip
    return Page(
        items=[ShipmentSummary.model_validate(i) for i in items],
        total=total, limit=page.limit, offset=page.offset,
    )  # fmt: skip


@router.get("/shipments/summary", response_model=ShipmentsSummary, summary="Shipment KPIs")
async def shipments_summary(session: Session) -> dict[str, Any]:
    return await queries.shipments_summary(session, await local_org_id(session))


@router.post(
    "/shipments/track",
    response_model=ShipmentDetail,
    status_code=200,
    summary="Track a reference with a carrier (creates or refreshes the shipment)",
)
async def track_reference(
    session: Session, registry: Registry, body: TrackRequest
) -> dict[str, Any]:
    value = normalize_reference(body.reference)
    ref_type = body.reference_type.value if body.reference_type else detect_reference_type(value)
    ref = TrackingReference(reference_type=ref_type, value=value)
    shipment_id = await _track(session, registry, body.carrier.lower(), ref)
    return await _detail_or_404(session, shipment_id)


@router.post(
    "/shipments/import",
    response_model=ImportReportOut,
    summary="Import shipment events from CSV (text/csv body; all-or-nothing)",
    openapi_extra={
        "requestBody": {"content": {"text/csv": {"schema": {"type": "string"}}}, "required": True}
    },
)
async def import_shipments(
    request: Request, session: Session, response: Response
) -> dict[str, Any]:
    body = await request.body()
    if len(body) > MAX_CSV_BYTES:
        raise ApiError(413, "file_too_large", "CSV import is limited to 5 MB")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiError(422, "invalid_encoding", "CSV must be UTF-8 encoded") from exc
    report = await import_csv(
        session, text, threshold_hours=get_settings().shipment_delay_threshold_hours
    )
    await session.commit()
    if not report.ok:
        response.status_code = 422
    return {
        "ok": report.ok,
        "rows": report.rows,
        "shipments_created": report.shipments_created,
        "shipments_updated": report.shipments_updated,
        "events_inserted": report.events_inserted,
        "events_duplicate": report.events_duplicate,
        "errors": [e.__dict__ for e in report.errors],
    }


@router.get("/shipments/{shipment_id}", response_model=ShipmentDetail, summary="Shipment details")
async def get_shipment(session: Session, shipment_id: uuid.UUID) -> dict[str, Any]:
    return await _detail_or_404(session, shipment_id)


@router.post(
    "/shipments/{shipment_id}/refresh",
    response_model=ShipmentDetail,
    summary="Refresh from carrier",
)
async def refresh_shipment(
    session: Session, registry: Registry, shipment_id: uuid.UUID
) -> dict[str, Any]:
    detail = await _detail_or_404(session, shipment_id)
    if not detail["refreshable"]:
        raise ApiError(
            409, "not_refreshable", "Imported shipments are updated by re-importing CSV data."
        )
    ref = TrackingReference(
        reference_type=detail["reference_type"], value=detail["reference_value"]
    )
    await _track(session, registry, detail["carrier"]["code"], ref)
    return await _detail_or_404(session, shipment_id)


@router.get(
    "/carriers/performance",
    response_model=list[CarrierPerformance],
    summary="Carrier comparison: integration status, fleet, shipment reliability",
)
async def carriers_performance(session: Session) -> list[dict[str, Any]]:
    return await queries.carrier_performance(session, await local_org_id(session))
