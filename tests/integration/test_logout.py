import pytest

LOGIN_URL = "/api/auth/login"
LOGOUT_URL = "/api/auth/logout"
REFRESH_URL = "/api/auth/refresh"


@pytest.mark.asyncio
async def test_logout_clears_cookie(client, admin_user, org):
    """Logout with a valid Bearer token — baseline happy path."""
    login_resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert login_resp.status_code == 200

    logout_resp = await client.post(
        LOGOUT_URL,
        # Bearer token is optional — include it here to confirm it's accepted
        headers={"Authorization": f"Bearer {login_resp.json()['access_token']}"},
        cookies=login_resp.cookies,
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out successfully"
    # Cookie must be cleared
    assert logout_resp.cookies.get("refresh_token", "") == ""


@pytest.mark.asyncio
async def test_logout_works_without_bearer_token(client, admin_user, org):
    """D-2 fix: logout must succeed using only the refresh cookie — no Bearer token."""
    login_resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert login_resp.status_code == 200

    # Deliberately send NO Authorization header — cookie only
    logout_resp = await client.post(
        LOGOUT_URL,
        cookies=login_resp.cookies,
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out successfully"


@pytest.mark.asyncio
async def test_logout_with_expired_access_token_still_works(client, admin_user, org):
    """D-2 fix: simulates the case where the access token has already expired.

    We omit the Authorization header entirely (same as an expired token from the
    client's perspective — the endpoint must not return 401 in either case).
    """
    login_resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert login_resp.status_code == 200
    refresh_cookies = login_resp.cookies

    # No Bearer header → simulates expired/missing access token
    logout_resp = await client.post(LOGOUT_URL, cookies=refresh_cookies)
    assert logout_resp.status_code == 200

    # Session must be revoked — refresh should now fail
    refresh_resp = await client.post(REFRESH_URL, cookies=refresh_cookies)
    assert refresh_resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client, admin_user, org):
    """After logout, the refresh cookie must not produce a new access token."""
    login_resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert login_resp.status_code == 200
    refresh_cookies = login_resp.cookies

    await client.post(LOGOUT_URL, cookies=refresh_cookies)

    refresh_resp = await client.post(REFRESH_URL, cookies=refresh_cookies)
    assert refresh_resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_without_cookie_returns_200(client, admin_user, org):
    """Logout with no cookie at all must still return 200 — client ends up logged out."""
    logout_resp = await client.post(LOGOUT_URL)
    assert logout_resp.status_code == 200


@pytest.mark.asyncio
async def test_logout_already_revoked_session_returns_200(client, admin_user, org):
    """Double-logout must return 200 — idempotent from the client's perspective."""
    login_resp = await client.post(
        LOGIN_URL,
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert login_resp.status_code == 200
    refresh_cookies = login_resp.cookies

    await client.post(LOGOUT_URL, cookies=refresh_cookies)
    second_logout = await client.post(LOGOUT_URL, cookies=refresh_cookies)
    assert second_logout.status_code == 200
