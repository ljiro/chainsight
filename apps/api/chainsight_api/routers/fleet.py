"""Fleet intelligence REST endpoints: vessels, tracks, port calls, ports, summary, alerts."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from chainsight_api.errors import ApiError
from chainsight_api.schemas.common import Page, PageParams
from chainsight_api.schemas.fleet import (
    AlertOut,
    ApproachingVessel,
    FleetSummary,
    PortCallOut,
    PortDetail,
    PortOut,
    TrackResponse,
    VesselDetail,
    VesselSummary,
)
from core.timeutils import ensure_utc, utcnow
from database.enums import DataMode, VesselClass
from database.session import session_scope
from services.fleet_tracking import queries
from services.fleet_tracking.queries import BBox, VesselFilters

router = APIRouter(tags=["fleet"])
Session = Annotated[AsyncSession, Depends(session_scope)]

MAX_TRACK_WINDOW = timedelta(days=31)


def _bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    try:
        return BBox.parse(raw)
    except ValueError as exc:
        raise ApiError(422, "invalid_bbox", str(exc)) from exc


def vessel_filters(
    bbox: Annotated[
        str | None, Query(description="minLon,minLat,maxLon,maxLat (minLon > maxLon crosses 180°)")
    ] = None,
    vessel_class: Annotated[list[VesselClass] | None, Query()] = None,
    carrier: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[str | None, Query(min_length=2, max_length=64)] = None,
    seen_within_s: Annotated[int | None, Query(ge=60, le=90 * 86400)] = None,
    data_mode: DataMode | None = None,
) -> VesselFilters:
    return VesselFilters(
        bbox=_bbox(bbox),
        classes=[c.value for c in vessel_class or []],
        carrier=carrier,
        q=q,
        seen_within_s=seen_within_s,
        data_mode=data_mode.value if data_mode else None,
    )


Filters = Annotated[VesselFilters, Depends(vessel_filters)]


@router.get("/fleet/positions", summary="Latest vessel positions as GeoJSON (for the map)")
async def fleet_positions(
    session: Session,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 5_000,
) -> dict[str, Any]:
    now = utcnow()
    if filters.seen_within_s is None:
        filters.seen_within_s = 24 * 3600  # the live map shows the last day by default
    vessels, truncated = await queries.fleet_positions(session, filters, now, limit=limit)
    features = []
    by_mode: dict[str, int] = {}
    for v in vessels:
        p = v["position"]
        mode = p["provenance"]["data_mode"] or "unknown"
        by_mode[mode] = by_mode.get(mode, 0) + 1
        features.append(
            {
                "type": "Feature",
                "id": str(v["id"]),
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": {
                    "id": str(v["id"]),
                    "mmsi": v["mmsi"],
                    "name": v["name"],
                    "vessel_class": v["vessel_class"],
                    "carrier": v["carrier"]["carrier_code"] if v["carrier"] else None,
                    "sog_knots": p["sog_knots"],
                    "cog_deg": p["cog_deg"],
                    "heading_deg": p["heading_deg"],
                    "nav_status": p["nav_status"],
                    "observed_at": p["observed_at"].isoformat(),
                    "age_seconds": p["age_seconds"],
                    "freshness": p["freshness"],
                    "source": p["provenance"]["source"],
                    "data_mode": mode,
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "generated_at": now.isoformat(),
            "count": len(features),
            "truncated": truncated,
            "by_data_mode": by_mode,
            "seen_within_s": filters.seen_within_s,
        },
    }


@router.get("/fleet/summary", response_model=FleetSummary, summary="Fleet activity overview")
async def fleet_summary(session: Session) -> dict[str, Any]:
    return await queries.fleet_summary(session, utcnow())


@router.get(
    "/fleet/approaching",
    response_model=list[ApproachingVessel],
    summary="Underway vessels heading toward a nearby port",
)
async def fleet_approaching(
    session: Session,
    within_nm: Annotated[float, Query(gt=0, le=200)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return await queries.approaching_vessels(session, utcnow(), within_nm=within_nm, limit=limit)


@router.get("/vessels", response_model=Page[VesselSummary], summary="Search vessels")
async def list_vessels(
    session: Session, filters: Filters, page: Annotated[PageParams, Query()]
) -> Page[VesselSummary]:
    items, total = await queries.list_vessels(
        session, filters, utcnow(), limit=page.limit, offset=page.offset
    )
    return Page(
        items=[VesselSummary.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def _vessel_or_404(session: AsyncSession, vessel_id: uuid.UUID) -> dict[str, Any]:
    vessel = await queries.get_vessel(session, vessel_id, utcnow())
    if vessel is None:
        raise ApiError(404, "vessel_not_found", "Vessel not found")
    return vessel


@router.get("/vessels/{vessel_id}", response_model=VesselDetail, summary="Vessel details")
async def get_vessel(session: Session, vessel_id: uuid.UUID) -> dict[str, Any]:
    return await _vessel_or_404(session, vessel_id)


@router.get(
    "/vessels/{vessel_id}/track",
    response_model=TrackResponse,
    summary="Historical track (downsampled; split at AIS gaps)",
)
async def vessel_track(
    session: Session,
    vessel_id: uuid.UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    max_points: Annotated[int, Query(ge=10, le=10_000)] = 2_000,
    gap_threshold_s: Annotated[int, Query(ge=60, le=86_400)] = 1_800,
) -> dict[str, Any]:
    await _vessel_or_404(session, vessel_id)
    try:
        end_at = ensure_utc(end) if end else utcnow()
        start_at = ensure_utc(start) if start else end_at - timedelta(hours=24)
    except ValueError as exc:
        raise ApiError(422, "invalid_time", "start/end must include a timezone offset") from exc
    if start_at >= end_at:
        raise ApiError(422, "invalid_time_range", "start must be before end")
    if end_at - start_at > MAX_TRACK_WINDOW:
        raise ApiError(422, "time_range_too_large", "track window is limited to 31 days")
    track = await queries.vessel_track(
        session, vessel_id, start_at, end_at, max_points=max_points, gap_threshold_s=gap_threshold_s
    )
    return {
        "vessel_id": vessel_id,
        "start": start_at,
        "end": end_at,
        "total_points": track.total_points,
        "returned_points": len(track.points),
        "geometry": {"type": "MultiLineString", "coordinates": track.segments},
        "points": track.points,
        "gaps": track.gaps,
        "gap_threshold_seconds": gap_threshold_s,
        "sources": track.sources,
    }


@router.get(
    "/vessels/{vessel_id}/port-calls",
    response_model=Page[PortCallOut],
    summary="Port call history (AIS geofence detection)",
)
async def vessel_port_calls(
    session: Session, vessel_id: uuid.UUID, page: Annotated[PageParams, Query()]
) -> Page[PortCallOut]:
    await _vessel_or_404(session, vessel_id)
    items, total = await queries.port_calls(
        session, vessel_id=vessel_id, limit=page.limit, offset=page.offset, now=utcnow()
    )
    return Page(
        items=[PortCallOut.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/ports", response_model=list[PortOut], summary="Ports (UN/LOCODE registry)")
async def list_ports(
    session: Session,
    bbox: str | None = None,
    q: Annotated[str | None, Query(min_length=2, max_length=64)] = None,
    active_only: Annotated[bool, Query(description="Only ports with calls in 30 days")] = False,
    limit: Annotated[int, Query(ge=1, le=5_000)] = 500,
) -> list[dict[str, Any]]:
    return await queries.list_ports(
        session, bbox=_bbox(bbox), q=q, active_only=active_only, limit=limit
    )


@router.get("/ports/{port_id}", response_model=PortDetail, summary="Port details")
async def get_port(session: Session, port_id: uuid.UUID) -> dict[str, Any]:
    port = await queries.get_port(session, port_id, utcnow())
    if port is None:
        raise ApiError(404, "port_not_found", "Port not found")
    return port


@router.get("/alerts", response_model=Page[AlertOut], summary="System alerts")
async def list_alerts(
    session: Session,
    page: Annotated[PageParams, Query()],
    status: Literal["open", "resolved", "all"] = "open",
    severity: Literal["info", "warning", "critical"] | None = None,
) -> Page[AlertOut]:
    items, total = await queries.list_alerts(
        session, status=status, severity=severity, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AlertOut.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
