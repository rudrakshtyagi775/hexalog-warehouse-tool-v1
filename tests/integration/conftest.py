import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.auth_service as _auth_svc
from app.database import AsyncSessionLocal
from app.models.organisation import Organisation


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _patch_side_effect_sessions(async_engine):
    """Redirect auth_service side-effect writes to the test database.

    _check_ip_rate_limit and _record_failed_attempt open their own sessions
    (separate from the main transaction so their commits always persist).
    Without this patch those sessions hit DATABASE_URL, not TEST_DATABASE_URL,
    causing rate-limit and failed-attempt writes to land in the wrong database.
    """
    test_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    _auth_svc._side_effect_session_factory = test_factory
    yield
    _auth_svc._side_effect_session_factory = AsyncSessionLocal


@pytest_asyncio.fixture(autouse=True)
async def _clear_side_effect_tables(async_engine):
    """Truncate tables written by side-effect sessions before each test.

    Side-effect sessions (rate-limiter, failed-attempt recorder) commit
    independently of the main db session, so their rows are NOT rolled back
    by the savepoint-based db fixture. Without this cleanup, login_attempts
    rows accumulate across tests and trigger 429 errors after 5 logins.
    """
    async with async_engine.connect() as conn:
        await conn.execute(text("DELETE FROM login_attempts"))
        await conn.commit()


@pytest_asyncio.fixture(scope="session")
async def committed_org(async_engine) -> Organisation:
    """A real-committed Organisation visible to READ COMMITTED side-effect sessions.

    Session-scoped so it is created once and stays in the DB for the whole
    test run. Brute-force tests that need side-effect sessions (_record_failed_attempt)
    to see their test users must use this org (and committed_session) rather than
    the savepoint-backed org/db fixtures.
    """
    RealSession = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with RealSession() as session:
        o = Organisation(name="BF Test Org", is_active=True)
        session.add(o)
        await session.commit()
        await session.refresh(o)
        return o


@pytest_asyncio.fixture
async def committed_session(async_engine) -> AsyncSession:
    """A real (non-savepoint) AsyncSession for tests that need side-effect sessions
    (READ COMMITTED) to see inserted rows. Commits are permanent; caller is
    responsible for using unique identifiers to avoid inter-test conflicts."""
    RealSession = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with RealSession() as session:
        yield session
