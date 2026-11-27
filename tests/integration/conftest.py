"""Integration fixtures: a throwaway PostGIS database migrated with Alembic.

Requires TEST_DATABASE_URL (database name must end in `_test`; it is dropped and recreated).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine, make_url

ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not TEST_DATABASE_URL:
                item.add_marker(pytest.mark.skip(reason="TEST_DATABASE_URL not set"))


def _reset_caches() -> None:
    from core.settings import get_settings
    from database import session

    get_settings.cache_clear()
    for fn in (session.get_async_engine, session.get_sync_engine, session.get_sessionmaker):
        fn.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL
    url = make_url(TEST_DATABASE_URL)
    if not (url.database or "").endswith("_test"):
        pytest.exit("Refusing to run: TEST_DATABASE_URL database name must end with '_test'", 2)

    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    _reset_caches()
    yield TEST_DATABASE_URL
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    _reset_caches()


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_config: Config, database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")
    engine = sa.create_engine(database_url)
    yield engine
    engine.dispose()


_ADMIN_KEY: tuple[str, str, str] | None = None


@pytest.fixture
def admin_api_key(migrated_db: Engine) -> str:
    """An admin-scoped API key in the local organization, for tests of data endpoints.
    Re-inserted when missing: the migration round-trip test recreates every table."""
    from database.enums import MemberRole
    from services.auth import crypto
    from services.auth.rbac import ROLE_PERMISSIONS

    global _ADMIN_KEY
    if _ADMIN_KEY is None:
        _ADMIN_KEY = crypto.new_api_key()
    key, prefix, key_hash = _ADMIN_KEY
    scopes = sorted(p.value for p in ROLE_PERMISSIONS[MemberRole.ADMIN])
    with migrated_db.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO api_keys (organization_id, name, key_prefix, key_hash, scopes) "
                "SELECT id, 'integration tests', :p, :h, :s FROM organizations WHERE slug = 'local' "
                "ON CONFLICT (key_prefix) DO NOTHING"
            ),
            {"p": prefix, "h": key_hash, "s": scopes},
        )
    return key


@pytest.fixture
def auth_headers(admin_api_key: str) -> dict[str, str]:
    return {"X-API-Key": admin_api_key}
