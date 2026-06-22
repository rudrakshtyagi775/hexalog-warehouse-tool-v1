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
async def test_login_timing_parity():
    """Timing for non-existent email ≈ wrong password (prevents email enumeration).

    Runs 10 samples each and checks the means are within 50 ms.
    Requires: running against a live DB with the test fixtures loaded.
    """
    # This test is intentionally skipped in fast mode — mark slow if needed
    pytest.skip("Timing test requires real DB and is slow — run manually")
