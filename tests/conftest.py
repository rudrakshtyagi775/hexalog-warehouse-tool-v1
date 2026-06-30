import asyncio
import os
import sys

import pytest
import pytest_asyncio

# asyncpg creates Futures bound to the running event loop. The Windows
# ProactorEventLoop spawns separate loops per fixture scope in pytest-asyncio
# 1.4.0, causing "Future attached to a different loop" errors. The Selector
# loop avoids this by keeping all async I/O on one loop instance.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure TEST_DATABASE_URL is available before any app import triggers config load
_test_db_url = os.environ.get("TEST_DATABASE_URL", "")

from sqlalchemy import text

from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole
from app.services.password_service import hash_password

# All PostgreSQL enum types used by ORM models with create_type=False.
# create_all() skips them so we must create them explicitly before table creation.
_ENUM_DDL = """
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role_enum') THEN
        CREATE TYPE user_role_enum AS ENUM ('admin', 'inward_operator', 'packer');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'audit_module_enum') THEN
        CREATE TYPE audit_module_enum AS ENUM ('shared', 'inward', 'outward', 'reports');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'customer_status_enum') THEN
        CREATE TYPE customer_status_enum AS ENUM ('active', 'inactive');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'counter_type_enum') THEN
        CREATE TYPE counter_type_enum AS ENUM ('outward_box', 'inscan_number', 'inward_box');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ledger_source_type_enum') THEN
        CREATE TYPE ledger_source_type_enum AS ENUM (
            'inward_submission', 'outward_scan', 'outward_scan_deletion', 'inward_scan_deletion'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'inward_box_status_enum') THEN
        CREATE TYPE inward_box_status_enum AS ENUM
            ('scanning', 'pending_verification', 'completed');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'inward_reference_status_enum') THEN
        CREATE TYPE inward_reference_status_enum AS ENUM ('open', 'completed');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'inward_code_type_enum') THEN
        CREATE TYPE inward_code_type_enum AS ENUM ('ean', 'style_code');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'outward_po_status_enum') THEN
        CREATE TYPE outward_po_status_enum AS ENUM ('open', 'closed');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'outward_box_status_enum') THEN
        CREATE TYPE outward_box_status_enum AS ENUM ('open', 'in_use', 'closed');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'outward_scan_result_enum') THEN
        CREATE TYPE outward_scan_result_enum AS ENUM ('accepted', 'rejected', 'deleted');
    END IF;
END $$;
"""


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    assert _test_db_url, "TEST_DATABASE_URL env var must be set for integration tests"
    engine = create_async_engine(_test_db_url, echo=False, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.execute(text(_ENUM_DDL))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(async_engine):
    """Function-scoped session that rolls back after each test.

    Uses an outer connection transaction + create_savepoint mode so that
    service-layer commit() calls only release a savepoint — the outer
    conn.rollback() undoes everything after the test, giving true isolation.
    """
    async with async_engine.connect() as conn:
        await conn.begin()
        async with AsyncSession(
            conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        await conn.rollback()


@pytest_asyncio.fixture
async def client(db):
    """httpx AsyncClient wired to the test database session."""
    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Seeded data ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def org(db) -> Organisation:
    o = Organisation(name="Test Org", is_active=True)
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture
async def admin_user(db, org) -> User:
    u = User(
        email="admin@test.com",
        password_hash=hash_password("AdminPass1!"),
        full_name="Test Admin",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(
        UserRole(user_id=u.id, organisation_id=org.id, role=UserRoleEnum.admin)
    )
    await db.flush()
    return u


@pytest_asyncio.fixture
async def packer_user(db, org) -> User:
    u = User(
        email="packer@test.com",
        password_hash=hash_password("PackerPass1!"),
        full_name="Test Packer",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(
        UserRole(user_id=u.id, organisation_id=org.id, role=UserRoleEnum.packer)
    )
    await db.flush()
    return u


@pytest_asyncio.fixture
async def inactive_user(db, org) -> User:
    u = User(
        email="inactive@test.com",
        password_hash=hash_password("InactivePass1!"),
        full_name="Inactive User",
        is_active=False,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    await db.flush()
    return u


@pytest_asyncio.fixture
async def inward_operator_user(db, org) -> User:
    u = User(
        email="inward@test.com",
        password_hash=hash_password("InwardPass1!"),
        full_name="Test Inward Operator",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserRole(user_id=u.id, organisation_id=org.id, role=UserRoleEnum.inward_operator))
    await db.flush()
    return u
