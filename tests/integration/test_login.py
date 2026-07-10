import statistics
import time

import pytest
from sqlalchemy import select

from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import Session as SessionModel
from app.models.user import User, UserOrganisation, UserRole
from app.services.password_service import hash_password

LOGIN_URL = "/api/auth/login"


@pytest.mark.asyncio
async def test_login_happy_path(client, admin_user, org):
    resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "admin@test.com"
    assert "admin" in data["roles"]
    assert data["organisation"]["id"] == org.id
    # Refresh token must be in httpOnly cookie, never in body
    assert "refresh_token" not in data
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client, admin_user, org):
    resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "WrongPassword!", "organisation_id": org.id},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_nonexistent_email(client, org):
    resp = await client.post(
        LOGIN_URL,
        json={"email": "nobody@test.com", "password": "SomePassword!", "organisation_id": org.id},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_inactive_user(client, inactive_user, org):
    resp = await client.post(
        LOGIN_URL,
        json={"email": "inactive@test.com", "password": "InactivePass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_wrong_organisation(client, admin_user):
    resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": 99999},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_no_password_hash_in_response(client, admin_user, org):
    resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200
    body_str = resp.text
    assert "password_hash" not in body_str
    assert "password" not in resp.json().get("user", {})


@pytest.mark.asyncio
async def test_login_timing_parity(client, admin_user, org):
    """Non-existent email path and wrong-password path must take similar wall-clock time.

    Both paths call verify_password() with a valid bcrypt hash:
      - missing email  → verify_password(password, DUMMY_HASH)
      - wrong password → verify_password(password, user.password_hash)

    5 samples each. Means must be within 150 ms (email enumeration prevention).
    """
    N = 5

    # Path 1: non-existent email → login() calls verify_password(password, DUMMY_HASH)
    nonexistent_times = []
    for _ in range(N):
        t0 = time.perf_counter()
        await client.post(
            LOGIN_URL,
            json={
                "email": "nobody@timing-check.test",
                "password": "SomePass1!",
                "organisation_id": org.id,
            },
        )
        nonexistent_times.append(time.perf_counter() - t0)

    # Path 2: correct email, wrong password → login() calls verify_password(password, user.password_hash)
    wrong_password_times = []
    for _ in range(N):
        t0 = time.perf_counter()
        await client.post(
            LOGIN_URL,
            json={
                "email": admin_user.email,
                "password": "WrongPassword1!",
                "organisation_id": org.id,
            },
        )
        wrong_password_times.append(time.perf_counter() - t0)

    mean_nonexistent = statistics.mean(nonexistent_times)
    mean_wrong = statistics.mean(wrong_password_times)

    # Both paths must actually trigger bcrypt — 12 rounds always takes > 50 ms
    assert mean_nonexistent > 0.05, (
        f"Non-existent email path is suspiciously fast ({mean_nonexistent * 1000:.1f} ms) — "
        "DUMMY_HASH may not be a valid bcrypt hash"
    )
    assert mean_wrong > 0.05, (
        f"Wrong-password path is suspiciously fast ({mean_wrong * 1000:.1f} ms)"
    )

    # Timing parity — prevents email enumeration via timing oracle
    diff = abs(mean_nonexistent - mean_wrong)
    assert diff < 0.15, (
        f"Timing parity violation (email enumeration risk): "
        f"nonexistent={mean_nonexistent * 1000:.1f} ms, "
        f"wrong_pw={mean_wrong * 1000:.1f} ms, "
        f"diff={diff * 1000:.1f} ms (limit=150 ms)"
    )


# ── Milestone 1: organisation_id becomes optional / auto-select ───────────────

@pytest.mark.asyncio
async def test_login_without_organisation_id_auto_selects_single_org(client, admin_user, org):
    """A user with exactly one active organisation is logged straight in
    without specifying organisation_id."""
    resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["organisation"]["id"] == org.id
    assert "access_token" in data
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_login_without_organisation_id_ignores_inactive_org_membership(db, client, org):
    """A membership in an inactive organisation must not count toward the
    'exactly one active organisation' auto-select rule."""
    inactive_org = Organisation(name="Inactive Org", is_active=False)
    db.add(inactive_org)
    await db.flush()

    u = User(
        email="single-active@test.com",
        password_hash=hash_password("Password1!"),
        full_name="Single Active Org User",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserOrganisation(user_id=u.id, organisation_id=inactive_org.id))
    db.add(UserRole(user_id=u.id, organisation_id=org.id, role=UserRoleEnum.admin))
    await db.flush()

    resp = await client.post(
        LOGIN_URL,
        json={"email": "single-active@test.com", "password": "Password1!"},
    )
    assert resp.status_code == 200
    assert resp.json()["organisation"]["id"] == org.id


@pytest.mark.asyncio
async def test_login_without_organisation_id_zero_active_orgs_returns_generic_failure(db, client):
    """A user with no organisation membership at all gets the same generic
    401 as any other authentication failure — no oracle."""
    u = User(
        email="no-org@test.com",
        password_hash=hash_password("Password1!"),
        full_name="No Org User",
        is_active=True,
    )
    db.add(u)
    await db.flush()

    resp = await client.post(
        LOGIN_URL,
        json={"email": "no-org@test.com", "password": "Password1!"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_without_organisation_id_only_inactive_org_returns_generic_failure(db, client):
    """A user whose only membership is an inactive organisation is treated
    the same as having zero active organisations."""
    inactive_org = Organisation(name="Solely Inactive Org", is_active=False)
    db.add(inactive_org)
    await db.flush()

    u = User(
        email="inactive-org-only@test.com",
        password_hash=hash_password("Password1!"),
        full_name="Inactive Org Only User",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=inactive_org.id))
    await db.flush()

    resp = await client.post(
        LOGIN_URL,
        json={"email": "inactive-org-only@test.com", "password": "Password1!"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_without_organisation_id_multiple_active_orgs_returns_placeholder(
    db, client, org
):
    """Milestone 1 scope: multi-org accounts get a placeholder response — no
    session, no tokens, no cookie. The picker UI is a later milestone."""
    second_org = Organisation(name="Second Org", is_active=True)
    db.add(second_org)
    await db.flush()

    u = User(
        email="multi-org@test.com",
        password_hash=hash_password("Password1!"),
        full_name="Multi Org User",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserOrganisation(user_id=u.id, organisation_id=second_org.id))
    await db.flush()

    resp = await client.post(
        LOGIN_URL,
        json={"email": "multi-org@test.com", "password": "Password1!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_organisation_selection"] is True
    returned_ids = {o["id"] for o in data["organisations"]}
    assert returned_ids == {org.id, second_org.id}
    assert "access_token" not in data
    assert "refresh_token" not in resp.cookies


@pytest.mark.asyncio
async def test_login_without_organisation_id_multiple_active_orgs_creates_no_session(
    db, client, org
):
    """Milestone 2 invariant: the placeholder response must not persist a
    sessions row — organisation selection alone must never establish auth."""
    second_org = Organisation(name="Second Org No Session", is_active=True)
    db.add(second_org)
    await db.flush()

    u = User(
        email="multi-org-no-session@test.com",
        password_hash=hash_password("Password1!"),
        full_name="Multi Org No Session User",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserOrganisation(user_id=u.id, organisation_id=second_org.id))
    await db.flush()

    resp = await client.post(
        LOGIN_URL,
        json={"email": "multi-org-no-session@test.com", "password": "Password1!"},
    )
    assert resp.status_code == 200
    assert resp.json()["requires_organisation_selection"] is True

    result = await db.execute(select(SessionModel).where(SessionModel.user_id == u.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_login_with_explicit_organisation_id_still_works_for_multi_org_user(
    db, client, org
):
    """Backward compatibility: existing clients that pass organisation_id
    must bypass the new auto-select/placeholder logic entirely, even for a
    multi-org account."""
    second_org = Organisation(name="Second Org BC", is_active=True)
    db.add(second_org)
    await db.flush()

    u = User(
        email="multi-org-bc@test.com",
        password_hash=hash_password("Password1!"),
        full_name="Multi Org BC User",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserOrganisation(user_id=u.id, organisation_id=second_org.id))
    await db.flush()

    resp = await client.post(
        LOGIN_URL,
        json={
            "email": "multi-org-bc@test.com",
            "password": "Password1!",
            "organisation_id": second_org.id,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["organisation"]["id"] == second_org.id
    assert "access_token" in data
