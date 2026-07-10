import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.enums import AuditModuleEnum, UserRoleEnum
from app.models.user import Session as SessionModel
from app.models.user import User, UserOrganisation, UserRole
from app.services.audit_service import write_audit_log
from app.services.jwt_service import issue_access_token
from app.services.password_service import DUMMY_HASH, verify_password

log = structlog.get_logger(__name__)

_INVALID_CREDENTIALS = "Invalid credentials"

# Session factory used by _check_ip_rate_limit and _record_failed_attempt.
# These helpers commit independently from the main transaction so their writes
# persist even when the caller rolls back. Tests override this name to point at
# the test database engine instead of the main one.
_side_effect_session_factory = AsyncSessionLocal


# ── Return types ──────────────────────────────────────────────────────────────

@dataclass
class LoginResult:
    access_token: str
    expires_at: datetime
    refresh_token: str
    user_id: int
    full_name: str
    email: str
    organisation_id: int
    organisation_name: str
    roles: list[UserRoleEnum]


@dataclass
class OrganisationOption:
    id: int
    name: str


@dataclass
class OrganisationChoiceRequired:
    """Returned by login() when organisation_id is omitted and the account has
    more than one active organisation. Milestone 1 placeholder only — no
    session or tokens are created for this result."""

    organisations: list[OrganisationOption]


@dataclass
class RefreshResult:
    access_token: str
    expires_at: datetime
    new_refresh_token: str


@dataclass
class SwitchOrgResult:
    access_token: str
    expires_at: datetime
    refresh_token: str
    organisation_id: int
    organisation_name: str
    roles: list[UserRoleEnum]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hash_refresh_token(raw_token: str) -> str:
    """HMAC-SHA256 of the raw token with the server-side hash key. Never persists raw token."""
    return hmac.new(
        settings.REFRESH_TOKEN_HASH_KEY.encode(),
        raw_token.encode(),
        hashlib.sha256,
    ).hexdigest()


async def _check_ip_rate_limit(ip_address: str | None) -> None:
    """Increment per-IP minute counter and raise 429 if the limit is exceeded.

    Uses a separate session so the increment commits regardless of whether the
    main login transaction succeeds or fails. Skips silently when IP is unknown.
    """
    if ip_address is None:
        return

    window_start = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    async with _side_effect_session_factory() as rate_session:
        result = await rate_session.execute(
            text("""
                INSERT INTO login_attempts (ip_address, window_start, attempt_count, created_at)
                VALUES (:ip, :window_start, 1, now())
                ON CONFLICT (ip_address, window_start)
                DO UPDATE SET attempt_count = login_attempts.attempt_count + 1
                RETURNING attempt_count
            """),
            {"ip": ip_address, "window_start": window_start},
        )
        attempt_count = result.scalar_one()
        await rate_session.commit()

    if attempt_count > settings.LOGIN_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )


async def _record_failed_attempt(user_id: int, current_failed_attempts: int) -> None:
    """Increment failed_attempts; lock account if threshold reached.

    Uses a separate session so this always persists even on main-tx rollback.
    """
    now = datetime.now(tz=UTC)
    new_count = current_failed_attempts + 1

    async with _side_effect_session_factory() as fa_session:
        if new_count >= settings.LOGIN_MAX_FAILURES:
            locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            await fa_session.execute(
                text(
                    "UPDATE users SET failed_attempts = :n, locked_until = :lu WHERE id = :uid"
                ),
                {"n": new_count, "lu": locked_until, "uid": user_id},
            )
        else:
            await fa_session.execute(
                text("UPDATE users SET failed_attempts = :n WHERE id = :uid"),
                {"n": new_count, "uid": user_id},
            )
        await fa_session.commit()


# ── Login ─────────────────────────────────────────────────────────────────────

async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    organisation_id: int | None = None,
    ip_address: str | None,
    user_agent: str | None,
) -> LoginResult | OrganisationChoiceRequired:
    await _check_ip_rate_limit(ip_address)

    # Load user + their org memberships in one round-trip
    stmt = (
        select(User)
        .where(User.email == email)
        .options(
            selectinload(User.user_organisations).selectinload(UserOrganisation.organisation)
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Always verify against *something* to prevent timing oracle on missing email
    if user is None:
        verify_password(password, DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    # Lockout check — before password verification to avoid bcrypt timing on locked account
    now = datetime.now(tz=UTC)
    if user.locked_until and now < user.locked_until:
        verify_password(password, DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    # Password check
    if not verify_password(password, user.password_hash):
        await _record_failed_attempt(user.id, user.failed_attempts)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    # is_active check — after successful password so we don't reveal active/inactive distinction
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    # organisation_id auto-resolution (Milestone 1) — only when the caller omits it.
    # Callers that pass organisation_id explicitly skip this block entirely and hit
    # the unchanged membership-check path below, preserving backward compatibility.
    if organisation_id is None:
        active_memberships = [
            uo
            for uo in user.user_organisations
            if uo.organisation is not None and uo.organisation.is_active
        ]
        if not active_memberships:
            # Same generic failure as an unmatched organisation_id today — no oracle.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS
            )
        if len(active_memberships) == 1:
            organisation_id = active_memberships[0].organisation_id
        else:
            return OrganisationChoiceRequired(
                organisations=[
                    OrganisationOption(id=uo.organisation_id, name=uo.organisation.name)
                    for uo in active_memberships
                ]
            )

    # Organisation membership check
    user_org = next(
        (uo for uo in user.user_organisations if uo.organisation_id == organisation_id),
        None,
    )
    if user_org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_CREDENTIALS)

    # Load roles for this specific org (separate query — selectinload can't filter by org)
    roles_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.organisation_id == organisation_id,
        )
    )
    roles = [ur.role for ur in roles_result.scalars().all()]

    # Reset failed attempts on successful login
    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(failed_attempts=0, locked_until=None)
    )

    # Create session
    raw_refresh_token = secrets.token_hex(32)
    token_hash = _hash_refresh_token(raw_refresh_token)
    session_id = uuid.uuid4()
    session_expires_at = now + timedelta(hours=settings.SESSION_ABSOLUTE_EXPIRE_HOURS)

    session = SessionModel(
        id=session_id,
        refresh_token_hash=token_hash,
        user_id=user.id,
        organisation_id=organisation_id,
        last_used_at=now,
        expires_at=session_expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)

    access_token, expires_at = issue_access_token(
        user_id=user.id,
        organisation_id=organisation_id,
        roles=roles,
        session_id=str(session_id),
    )

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="auth.login",
        resource_type="users",
        resource_id=user.id,
        user_id=user.id,
        organisation_id=organisation_id,
        after_data={"email": user.email, "organisation_id": organisation_id},
        ip_address=ip_address,
    )

    await db.commit()

    org_name = user_org.organisation.name if user_org.organisation else ""
    return LoginResult(
        access_token=access_token,
        expires_at=expires_at,
        refresh_token=raw_refresh_token,
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        organisation_id=organisation_id,
        organisation_name=org_name,
        roles=roles,
    )


# ── Refresh ───────────────────────────────────────────────────────────────────

async def refresh_session(
    db: AsyncSession, raw_token: str, *, ip_address: str | None = None
) -> RefreshResult:
    """Rotate the refresh token and issue a new access token. 5-case flow per June 18 design."""
    token_hash = _hash_refresh_token(raw_token)
    now = datetime.now(tz=UTC)

    # SELECT FOR UPDATE — prevents concurrent refresh calls from double-rotating
    stmt = (
        select(SessionModel)
        .where(SessionModel.refresh_token_hash == token_hash)
        .with_for_update()
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session is None:
        # Token not found as current hash — check if it's a previous (rotated) token
        prev_stmt = (
            select(SessionModel)
            .where(SessionModel.previous_refresh_token_hash == token_hash)
            .with_for_update()
        )
        prev_result = await db.execute(prev_stmt)
        prev_session = prev_result.scalar_one_or_none()

        if prev_session is None:
            # Case 1: Unknown token entirely
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session not found — please log in again",
            )

        # Case 2: Found in previous hash — grace window check
        if (
            prev_session.previous_token_valid_until
            and now <= prev_session.previous_token_valid_until
        ):
            # Still in grace window — overlapping browser refresh call, do not revoke
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Refresh already rotated — retry with current cookie",
            )

        # Grace window expired — replay of old token = theft detected
        prev_session.revoked_at = now
        prev_session.revoke_reason = "theft_detected"
        await write_audit_log(
            db,
            module=AuditModuleEnum.shared,
            action="session.theft_detected",
            resource_type="sessions",
            user_id=prev_session.user_id,
            organisation_id=prev_session.organisation_id,
            ip_address=ip_address,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Security alert: session revoked due to suspected token theft",
        )

    # Case 3: Found as current hash but already revoked
    if session.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked — please log in again",
        )

    # Case 4: Expired (hard cap)
    if session.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired — please log in again",
        )

    # Case 5: Valid — check inactivity then rotate
    inactivity_limit = timedelta(minutes=settings.SESSION_INACTIVITY_MINUTES)
    if (now - session.last_used_at) > inactivity_limit:
        session.revoked_at = now
        session.revoke_reason = "inactivity_timeout"
        await write_audit_log(
            db,
            module=AuditModuleEnum.shared,
            action="session.inactivity_timeout",
            resource_type="sessions",
            user_id=session.user_id,
            organisation_id=session.organisation_id,
            ip_address=ip_address,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to inactivity",
        )

    # Rotate refresh token
    new_raw_token = secrets.token_hex(32)
    new_token_hash = _hash_refresh_token(new_raw_token)

    session.previous_refresh_token_hash = session.refresh_token_hash
    session.previous_token_valid_until = now + timedelta(
        seconds=settings.REFRESH_REUSE_GRACE_SECONDS
    )
    session.refresh_token_hash = new_token_hash
    session.last_used_at = now

    # Re-fetch current roles (roles can change mid-session)
    roles_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == session.user_id,
            UserRole.organisation_id == session.organisation_id,
        )
    )
    roles = [ur.role for ur in roles_result.scalars().all()]

    access_token, expires_at = issue_access_token(
        user_id=session.user_id,
        organisation_id=session.organisation_id,
        roles=roles,
        session_id=str(session.id),
    )

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="auth.token_refresh",
        resource_type="sessions",
        user_id=session.user_id,
        organisation_id=session.organisation_id,
        ip_address=ip_address,
    )

    await db.commit()

    return RefreshResult(
        access_token=access_token,
        expires_at=expires_at,
        new_refresh_token=new_raw_token,
    )


# ── Logout ────────────────────────────────────────────────────────────────────

async def logout(
    db: AsyncSession,
    raw_token: str,
    ip_address: str | None,
) -> None:
    """Revoke the session identified by the refresh token cookie.

    Deliberately does NOT require a valid access token — the cookie alone is
    sufficient. user_id and organisation_id are read from the session row so
    that logout works even after the access token has expired.
    """
    token_hash = _hash_refresh_token(raw_token)

    result = await db.execute(
        select(SessionModel).where(
            SessionModel.refresh_token_hash == token_hash,
            SessionModel.revoked_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()

    user_id: int | None = None
    organisation_id: int | None = None

    if session:
        user_id = session.user_id
        organisation_id = session.organisation_id
        session.revoked_at = datetime.now(tz=UTC)
        session.revoke_reason = "logout"

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="auth.logout",
        resource_type="sessions",
        user_id=user_id,
        organisation_id=organisation_id,
        ip_address=ip_address,
    )
    await db.commit()


# ── Logout-all ────────────────────────────────────────────────────────────────

async def switch_organisation(
    db: AsyncSession,
    *,
    user_id: int,
    from_organisation_id: int,
    target_org_id: int,
    ip_address: str | None,
    user_agent: str | None,
) -> SwitchOrgResult:
    """Create a new session for target_org_id and issue a fresh token pair.

    The old session is intentionally left alive — the browser's cookie is
    replaced, so it can no longer be refreshed and will die by inactivity
    or absolute expiry. Per the June 18 redesign: old session is NOT revoked.
    """
    # Verify membership and load org name in one round-trip
    membership_result = await db.execute(
        select(UserOrganisation)
        .where(
            UserOrganisation.user_id == user_id,
            UserOrganisation.organisation_id == target_org_id,
        )
        .options(selectinload(UserOrganisation.organisation))
    )
    user_org = membership_result.scalar_one_or_none()
    if user_org is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of that organisation",
        )

    # Roles for the target org (may differ from current org)
    roles_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.organisation_id == target_org_id,
        )
    )
    roles = [ur.role for ur in roles_result.scalars().all()]

    # New session
    now = datetime.now(tz=UTC)
    raw_token = secrets.token_hex(32)
    token_hash = _hash_refresh_token(raw_token)
    session_id = uuid.uuid4()
    session_expires_at = now + timedelta(hours=settings.SESSION_ABSOLUTE_EXPIRE_HOURS)

    new_session = SessionModel(
        id=session_id,
        refresh_token_hash=token_hash,
        user_id=user_id,
        organisation_id=target_org_id,
        last_used_at=now,
        expires_at=session_expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(new_session)

    access_token, expires_at = issue_access_token(
        user_id=user_id,
        organisation_id=target_org_id,
        roles=roles,
        session_id=str(session_id),
    )

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="auth.switch_organisation",
        resource_type="sessions",
        user_id=user_id,
        organisation_id=target_org_id,
        after_data={
            "from_organisation_id": from_organisation_id,
            "to_organisation_id": target_org_id,
        },
        ip_address=ip_address,
    )

    await db.commit()

    org_name = user_org.organisation.name if user_org.organisation else ""
    return SwitchOrgResult(
        access_token=access_token,
        expires_at=expires_at,
        refresh_token=raw_token,
        organisation_id=target_org_id,
        organisation_name=org_name,
        roles=roles,
    )


async def logout_all_devices(
    db: AsyncSession,
    current_session_id: str,
    user_id: int,
    organisation_id: int,
    ip_address: str | None,
) -> int:
    """Revoke all sessions for this user except the current one. Returns revoked count."""
    now = datetime.now(tz=UTC)

    result = await db.execute(
        update(SessionModel)
        .where(
            SessionModel.user_id == user_id,
            SessionModel.id != uuid.UUID(current_session_id),
            SessionModel.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason="all_sessions_logout")
    )
    revoked_count = result.rowcount

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="auth.logout_all_devices",
        resource_type="sessions",
        user_id=user_id,
        organisation_id=organisation_id,
        after_data={"revoked_session_count": revoked_count},
        ip_address=ip_address,
    )
    await db.commit()
    return revoked_count


# ── Admin session management ──────────────────────────────────────────────────

async def revoke_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    admin_user_id: int,
    admin_org_id: int,
    ip_address: str | None,
) -> None:
    """Revoke a single session by ID. Only sessions in the admin's org can be revoked.

    Returns 404 for sessions that don't exist, belong to a different org, or are
    already revoked — all indistinguishable to the caller to prevent oracle leakage.
    """
    now = datetime.now(tz=UTC)

    result = await db.execute(
        select(SessionModel)
        .where(
            SessionModel.id == session_id,
            SessionModel.organisation_id == admin_org_id,
            SessionModel.revoked_at.is_(None),
        )
        .with_for_update()
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    target_user_id = session.user_id
    session.revoked_at = now
    session.revoked_by = admin_user_id
    session.revoke_reason = "admin_force"

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="session.force_revoked",
        resource_type="sessions",
        user_id=admin_user_id,
        organisation_id=admin_org_id,
        after_data={"target_session_id": str(session_id), "target_user_id": target_user_id},
        ip_address=ip_address,
    )
    await db.commit()


async def revoke_user_sessions(
    db: AsyncSession,
    *,
    target_user_id: int,
    admin_user_id: int,
    admin_org_id: int,
    ip_address: str | None,
) -> int:
    """Revoke all active sessions for a user within the admin's organisation.

    Verifies the target user is a member of the admin's org before acting.
    Returns 404 if the user is not a member (avoids user-ID enumeration across orgs).
    Sessions are filtered by organisation_id so cross-org sessions are untouched.
    """
    membership = await db.execute(
        select(UserOrganisation).where(
            UserOrganisation.user_id == target_user_id,
            UserOrganisation.organisation_id == admin_org_id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this organisation",
        )

    now = datetime.now(tz=UTC)
    result = await db.execute(
        update(SessionModel)
        .where(
            SessionModel.user_id == target_user_id,
            SessionModel.organisation_id == admin_org_id,
            SessionModel.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=admin_user_id, revoke_reason="admin_force")
    )
    revoked_count = result.rowcount

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="session.all_revoked",
        resource_type="sessions",
        user_id=admin_user_id,
        organisation_id=admin_org_id,
        after_data={"target_user_id": target_user_id, "revoked_count": revoked_count},
        ip_address=ip_address,
    )
    await db.commit()
    return revoked_count
