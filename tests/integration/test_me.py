import pytest

ME_URL = "/api/auth/me"
LOGIN_URL = "/api/auth/login"


async def _get_token(client, org, email="admin@test.com", password="AdminPass1!"):
    resp = await client.post(
        LOGIN_URL,
        json={"email": email, "password": password, "organisation_id": org.id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_me_happy_path(client, admin_user, org):
    token = await _get_token(client, org)
    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@test.com"
    assert data["user_id"] == admin_user.id
    assert data["organisation_id"] == org.id
    assert "admin" in data["roles"]
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_me_no_password_hash_in_response(client, admin_user, org):
    token = await _get_token(client, org)
    resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "password_hash" not in resp.text


@pytest.mark.asyncio
async def test_me_missing_token(client):
    resp = await client.get(ME_URL)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_bad_token(client):
    resp = await client.get(ME_URL, headers={"Authorization": "Bearer not.a.valid.token"})
    assert resp.status_code == 401
