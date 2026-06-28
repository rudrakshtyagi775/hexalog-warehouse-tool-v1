import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure TEST_DATABASE_URL is available before any app import triggers config load
_test_db_url = os.environ.get("TEST_DATABASE_URL", "")

from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole
from app.services.password_service import hash_password


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    assert _test_db_url, "TEST_DATABASE_URL env var must be set for integration tests"
    engine = create_async_engine(_test_db_url, echo=False, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(async_engine):
    """Function-scoped session that rolls back after each test."""
    TestSession = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with TestSession() as session:
        yield session
        await session.rollback()


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
