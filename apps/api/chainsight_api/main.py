"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chainsight_api.errors import install_error_handlers
from chainsight_api.health import close_redis
from chainsight_api.middleware import install_middleware
from chainsight_api.routers import catalog, fleet, health, live
from chainsight_api.schemas.common import ErrorResponse
from core.logging_config import configure_logging, get_logger
from core.settings import Settings, get_settings
from database.session import get_async_engine

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log = get_logger(__name__)
    settings: Settings = app.state.settings
    log.info("startup", env=settings.app_env.value, version=settings.app_version)
    yield
    await close_redis()
    if get_async_engine.cache_info().currsize:
        await get_async_engine().dispose()
    log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="ChainSight API",
        version=settings.app_version,
        description=(
            "Maritime intelligence, shipment visibility, and quantitative research API. "
            "Every data-bearing response carries provenance and data-mode information."
        ),
        openapi_url=f"{API_V1_PREFIX}/openapi.json",
        docs_url=f"{API_V1_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
        responses={
            422: {"model": ErrorResponse, "description": "Validation error"},
            500: {"model": ErrorResponse, "description": "Internal error"},
        },
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    install_middleware(app)
    install_error_handlers(app)

    v1 = APIRouter(prefix=API_V1_PREFIX)
    v1.include_router(health.router)
    v1.include_router(catalog.router)
    v1.include_router(fleet.router)
    v1.include_router(live.router)
    app.include_router(v1)
    return app


app = create_app()
