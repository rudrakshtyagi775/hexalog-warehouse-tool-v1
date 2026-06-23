import statistics
import time

import pytest
import pytest_asyncio


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
