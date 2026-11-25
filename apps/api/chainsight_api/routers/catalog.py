"""Data sources and carriers: the platform's reference catalog."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, with_loader_criteria

from chainsight_api.schemas.catalog import (
    CarrierIntegrationOut,
    CarrierOut,
    DataSourceOut,
    Freshness,
)
from chainsight_api.schemas.common import Page, PageParams
from core.timeutils import utcnow
from database.enums import CarrierType, DataSourceCategory, DataSourceStatus
from database.models import Carrier, CarrierIntegration, DataSource
from database.session import session_scope

router = APIRouter(tags=["catalog"])

Session = Annotated[AsyncSession, Depends(session_scope)]


def compute_freshness(
    last_success_at: datetime | None, expected_interval_s: int | None, now: datetime
) -> Freshness:
    """`stale` once the source is more than 2 expected intervals overdue."""
    if last_success_at is None:
        return "never"
    if expected_interval_s is None:
        return "unknown"
    return (
        "fresh" if now - last_success_at <= timedelta(seconds=2 * expected_interval_s) else "stale"
    )


@router.get("/data-sources", response_model=Page[DataSourceOut], summary="List data sources")
async def list_data_sources(
    session: Session,
    page: Annotated[PageParams, Query()],
    category: DataSourceCategory | None = None,
    status: DataSourceStatus | None = None,
) -> Page[DataSourceOut]:
    stmt = sa.select(DataSource)
    if category is not None:
        stmt = stmt.where(DataSource.category == category)
    if status is not None:
        stmt = stmt.where(DataSource.status == status)

    total = (
        await session.execute(sa.select(sa.func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(stmt.order_by(DataSource.name).limit(page.limit).offset(page.offset))
    ).scalars()

    now = utcnow()
    items = [
        DataSourceOut.model_validate(
            {
                **{f: getattr(ds, f) for f in DataSourceOut.model_fields if f != "freshness"},
                "freshness": compute_freshness(
                    ds.last_success_at, ds.expected_update_interval_seconds, now
                ),
            }
        )
        for ds in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/carriers", response_model=Page[CarrierOut], summary="List carriers and integration status"
)
async def list_carriers(
    session: Session,
    page: Annotated[PageParams, Query()],
    carrier_type: CarrierType | None = None,
    sort: Literal["name", "-name", "code", "-code"] = "name",
) -> Page[CarrierOut]:
    stmt = sa.select(Carrier)
    if carrier_type is not None:
        stmt = stmt.where(Carrier.carrier_type == carrier_type)

    total = (
        await session.execute(sa.select(sa.func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    column = Carrier.name if sort.lstrip("-") == "name" else Carrier.code
    order = column.desc() if sort.startswith("-") else column.asc()
    rows = (
        await session.execute(
            stmt.options(
                selectinload(Carrier.integrations),
                # Until auth lands (Phase 6) only platform-level integrations are visible.
                with_loader_criteria(
                    CarrierIntegration, CarrierIntegration.organization_id.is_(None)
                ),
            )
            .order_by(order)
            .limit(page.limit)
            .offset(page.offset)
        )
    ).scalars()

    items = [
        CarrierOut(
            code=c.code,
            name=c.name,
            scac=c.scac,
            carrier_type=c.carrier_type,
            website_url=c.website_url,
            integrations=[
                CarrierIntegrationOut.model_validate(i)
                for i in sorted(c.integrations, key=lambda i: i.capability.value)
            ],
        )
        for c in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
