"""Engine and session factories (psycopg 3 for both async and sync, ADR-005)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.settings import get_settings


@lru_cache
def get_async_engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        str(s.database_url),
        pool_size=s.database_pool_size,
        pool_pre_ping=True,
        echo=s.database_echo,
    )


@lru_cache
def get_sync_engine() -> Engine:
    s = get_settings()
    return create_engine(str(s.database_url), pool_pre_ping=True, echo=s.database_echo)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


async def session_scope() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with get_sessionmaker()() as session:
        yield session
