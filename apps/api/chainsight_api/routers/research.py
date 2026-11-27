"""Quantitative research endpoints: indicators, features, analysis, experiments, backtests.

Analyses and backtests are CPU-bound and synchronous; they run in a worker thread. Every
response states its time alignment and carries a research disclaimer.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection
from starlette.concurrency import run_in_threadpool

from chainsight_api.auth import ReadPrincipal, client_ip, require
from chainsight_api.errors import ApiError
from database.session import get_sessionmaker, get_sync_engine
from ml.quant_research.features import FeatureError
from ml.quant_research.stats import InsufficientData
from services import audit
from services import research_service as rs
from services.auth.rbac import Permission
from services.auth.service import Principal

router = APIRouter(prefix="/research", tags=["research"])
Writer = Annotated[Principal, Depends(require(Permission.RESEARCH_WRITE))]


async def _audit(request: Request, principal: Principal, action: str, eid: Any) -> None:
    async with get_sessionmaker()() as session, session.begin():
        await audit.record(session, action, principal=principal, entity_type="research_experiment",
                           entity_id=eid, ip=client_ip(request))  # fmt: skip


async def _read[T](fn: Callable[[Connection], T]) -> T:
    def run() -> T:
        with get_sync_engine().connect() as conn:
            return fn(conn)

    return await _guard(run)


async def _guard[T](fn: Callable[[], T]) -> T:
    try:
        return await run_in_threadpool(fn)
    except rs.NotFound as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except InsufficientData as exc:
        raise ApiError(422, "insufficient_data", str(exc)) from exc
    except (FeatureError, ValueError, TypeError) as exc:
        raise ApiError(422, "invalid_research_request", str(exc)) from exc


@router.get("/indicators", summary="Base indicators with coverage, source and availability rules")
async def indicators() -> dict[str, Any]:
    return await _read(rs.indicators)


@router.get("/features", summary="A feature's history (point-in-time availability per value)")
async def feature(
    key: Annotated[str, Query(max_length=300, description="base|frequency|transform")],
) -> dict[str, Any]:
    return await _read(lambda c: rs.feature_series(c, key))


@router.get("/market", summary="A market series' history")
async def market(key: Annotated[str, Query(max_length=100)]) -> dict[str, Any]:
    return await _read(lambda c: rs.market_series(c, key))


class AnalysisRequest(BaseModel):
    feature: str = Field(max_length=300)
    asset: str = Field(max_length=100)
    execution_lag_days: int = Field(default=0, ge=0, le=10)
    max_lag: int = Field(default=8, ge=1, le=26)
    rolling_window: int = Field(default=52, ge=8, le=260)
    min_train: int = Field(default=104, ge=26, le=520)


@router.post(
    "/analysis", summary="Correlation, lags, stationarity, HAC regression, Granger, OOS R²"
)
async def analysis(body: AnalysisRequest) -> dict[str, Any]:
    return await _read(lambda c: rs.analysis(c, **body.model_dump()))


class ImportanceRequest(BaseModel):
    features: list[str] = Field(min_length=2, max_length=12)
    asset: str = Field(max_length=100)


@router.post("/importance", summary="Permutation feature importance on a later held-out period")
async def importance(body: ImportanceRequest) -> dict[str, Any]:
    return await _read(lambda c: rs.importance(c, features=body.features, asset=body.asset))


@router.get("/experiments", summary="Research experiments with their latest backtest summary")
async def experiments(principal: ReadPrincipal) -> list[dict[str, Any]]:
    return await _read(lambda c: rs.experiments(c, principal.organization_id))


@router.get(
    "/experiments/multiple-testing",
    summary="Deflated Sharpe ratios and FDR-adjusted p-values across all experiments",
)
async def multiple_testing(principal: ReadPrincipal) -> dict[str, Any]:
    return await _read(lambda c: rs.multiple_testing(c, principal.organization_id))


class ExperimentRequest(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    hypothesis: str = Field(
        min_length=10, max_length=2000, description="State it before seeing results"
    )
    config: dict[str, Any]


@router.post("/experiments", status_code=201, summary="Create an experiment and run its backtest")
async def create_experiment(
    request: Request, principal: Writer, body: ExperimentRequest
) -> dict[str, Any]:
    org = principal.organization_id
    detail = await _guard(lambda: rs.create_and_run(get_sync_engine(), org, **body.model_dump()))
    await _audit(request, principal, "experiment.created", detail["id"])
    return detail


@router.get(
    "/experiments/{experiment_id}", summary="Experiment with runs, metrics, folds and equity"
)
async def experiment(principal: ReadPrincipal, experiment_id: uuid.UUID) -> dict[str, Any]:
    return await _read(lambda c: rs.experiment_detail(c, principal.organization_id, experiment_id))


@router.post("/experiments/{experiment_id}/runs", summary="Re-run an experiment's backtest")
async def rerun(request: Request, principal: Writer, experiment_id: uuid.UUID) -> dict[str, Any]:
    org = principal.organization_id
    detail = await _guard(lambda: rs.rerun(get_sync_engine(), org, experiment_id))
    await _audit(request, principal, "experiment.run", experiment_id)
    return detail
