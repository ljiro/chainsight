"""Predictive intelligence endpoints: models, ETA, delay risk, congestion, anomalies.

Every prediction carries its model version, prediction time, input-data time and
uncertainty; models trained on synthetic data are flagged in every response.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutils import utcnow
from database.session import session_scope
from services import prediction_queries as pq

router = APIRouter(tags=["predictions"])
Session = Annotated[AsyncSession, Depends(session_scope)]


@router.get("/models", summary="Model versions with their latest evaluations")
async def list_models(session: Session) -> list[dict[str, Any]]:
    return await pq.models(session)


@router.get("/predictions/summary", summary="Predictive intelligence KPIs")
async def predictions_summary(session: Session) -> dict[str, Any]:
    return await pq.summary(session, utcnow())


@router.get("/predictions/eta", summary="Latest ETA prediction per vessel (last 24 h)")
async def eta_predictions(
    session: Session, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[dict[str, Any]]:
    return await pq.latest_eta(session, since=utcnow() - timedelta(hours=24), limit=limit)


@router.get("/vessels/{vessel_id}/eta", summary="ETA prediction history for a vessel")
async def vessel_eta(session: Session, vessel_id: uuid.UUID) -> list[dict[str, Any]]:
    return await pq.vessel_eta_history(session, vessel_id)


@router.get("/predictions/delay", summary="Latest delay probability per active shipment")
async def delay_predictions(
    session: Session, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[dict[str, Any]]:
    return await pq.latest_delay(session, limit=limit)


@router.get(
    "/shipments/{shipment_id}/delay-risk", summary="Delay probability history for a shipment"
)
async def shipment_delay_risk(session: Session, shipment_id: uuid.UUID) -> list[dict[str, Any]]:
    return await pq.shipment_delay_history(session, shipment_id)


@router.get("/predictions/congestion", summary="Port congestion: current level and 24 h forecast")
async def congestion(
    session: Session, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[dict[str, Any]]:
    return await pq.congestion_overview(session, utcnow(), limit=limit)


@router.get(
    "/ports/{port_id}/congestion", summary="Port congestion observations (72 h) and forecast"
)
async def port_congestion(session: Session, port_id: uuid.UUID) -> dict[str, Any]:
    return await pq.port_congestion(session, port_id, utcnow())


@router.get("/anomalies", summary="Vessel anomaly alerts (rule-based)")
async def list_anomalies(
    session: Session,
    vessel_id: uuid.UUID | None = None,
    status: Literal["open", "resolved", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return await pq.anomalies(session, vessel_id=vessel_id, status=status, limit=limit)
