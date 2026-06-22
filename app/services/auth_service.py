import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.enums import AuditModuleEnum, UserRoleEnum
from app.models.user import Session as SessionModel, User, UserOrganisation, UserRole
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
class RefreshResult:
    access_token: str
    expires_at: datetime
    new_refresh_token: str


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

    window_start = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
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
    now = datetime.now(tz=timezone.utc)
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
    organisation_id: int,
    ip_address: str | None,
    user_agent: str | None,
) -> LoginResult:
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
    now = datetime.now(tz=timezone.utc)
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
    now = datetime.now(tz=timezone.utc)

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
        session.revoked_at = datetime.now(tz=timezone.utc)
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

async def logout_all_devices(
    db: AsyncSession,
    current_session_id: str,
    user_id: int,
    organisation_id: int,
    ip_address: str | None,
) -> int:
    """Revoke all sessions for this user except the current one. Returns revoked count."""
    now = datetime.now(tz=timezone.utc)

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
