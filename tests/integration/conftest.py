import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.auth_service as _auth_svc
from app.database import AsyncSessionLocal


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
