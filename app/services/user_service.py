"""User management service — CRUD operations for users within an organisation.

All public functions accept an AsyncSession and return Pydantic response objects.
None of them commit — the caller (router) owns the transaction boundary.

Two-query pattern for list operations avoids N+1:
  1. Fetch all users in org via user_organisations join.
  2. Fetch all their roles for this org in one query using user_id.in_(user_ids).
  Then group by user_id using collections.defaultdict.
"""

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuditModuleEnum, UserRoleEnum  # noqa: F401 (used by later tasks)
from app.models.user import User, UserOrganisation, UserRole
from app.models.user import Session as SessionModel
from app.schemas.user import UserResponse, UserRoleInfo
from app.services.audit_service import write_audit_log
from app.services.password_service import hash_password


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Thin wrapper around password_service.hash_password for use within this module."""
    return hash_password(password)


async def _get_user_in_org(db: AsyncSession, *, user_id: int, org_id: int) -> User:
    """Return a User only if they belong to org_id. Raises HTTP 404 otherwise."""
    result = await db.execute(
        select(User)
        .join(UserOrganisation, UserOrganisation.user_id == User.id)
        .where(User.id == user_id, UserOrganisation.organisation_id == org_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _build_user_response(db: AsyncSession, user_id: int, org_id: int) -> UserResponse:
    """Fetch user + org-scoped roles and assemble a UserResponse (no password_hash).

    Used by Tasks 3–7 after commit(), when the ORM instance may have expired.
    Re-fetches by primary key so it works regardless of session state.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    roles_result = await db.execute(
        select(UserRole)
        .where(UserRole.user_id == user_id, UserRole.organisation_id == org_id)
        .order_by(UserRole.created_at)
    )
    org_roles = list(roles_result.scalars().all())

    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        created_at=user.created_at,
        roles=[UserRoleInfo(role=r.role, created_at=r.created_at) for r in org_roles],
    )


# ── Public service functions ───────────────────────────────────────────────────

async def list_org_users(db: AsyncSession, *, org_id: int) -> list[UserResponse]:
    """Return all users in the org with their org-scoped roles, ordered by full_name.

    Two-query implementation to avoid N+1:
      Q1: users in org (join user_organisations)
      Q2: all roles for those users in this org (single IN query)
    """
    users_result = await db.execute(
        select(User)
        .join(UserOrganisation, UserOrganisation.user_id == User.id)
        .where(UserOrganisation.organisation_id == org_id)
        .order_by(User.full_name)
    )
    users = list(users_result.scalars().all())
    if not users:
        return []

    user_ids = [u.id for u in users]
    roles_result = await db.execute(
        select(UserRole)
        .where(UserRole.user_id.in_(user_ids), UserRole.organisation_id == org_id)
        .order_by(UserRole.created_at)
    )
    all_roles = list(roles_result.scalars().all())

    roles_by_user: dict[int, list[UserRole]] = defaultdict(list)
    for role in all_roles:
        roles_by_user[role.user_id].append(role)

    return [
        UserResponse(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            is_active=u.is_active,
            created_at=u.created_at,
            roles=[
                UserRoleInfo(role=r.role, created_at=r.created_at)
                for r in roles_by_user[u.id]
            ],
        )
        for u in users
    ]


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str,
    roles: list[UserRoleEnum],
    org_id: int,
    creating_user_id: int,
    ip_address: str | None,
) -> UserResponse:
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=email,
        full_name=full_name,
        password_hash=_hash_password(password),
        is_active=True,
    )
    db.add(user)
    await db.flush()

    db.add(UserOrganisation(
        user_id=user.id,
        organisation_id=org_id,
        created_by=creating_user_id,
    ))

    for role in roles:
        db.add(UserRole(
            user_id=user.id,
            organisation_id=org_id,
            role=role,
            assigned_by=creating_user_id,
        ))

    await db.flush()

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.user.create",
        resource_type="users",
        resource_id=user.id,
        user_id=creating_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={
            "email": email,
            "full_name": full_name,
            "is_active": True,
            "roles": [r.value for r in roles],
        },
    )

    await db.commit()
    return await _build_user_response(db, user.id, org_id)


async def update_user(
    db: AsyncSession,
    *,
    user_id: int,
    org_id: int,
    full_name: str | None,
    is_active: bool | None,
    current_user_id: int,
    ip_address: str | None,
) -> UserResponse:
    user = await _get_user_in_org(db, user_id=user_id, org_id=org_id)

    if full_name is None and is_active is None:
        return await _build_user_response(db, user.id, org_id)

    if is_active is False and user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin cannot deactivate their own account",
        )

    before_data = {"full_name": user.full_name, "is_active": user.is_active}

    if full_name is not None:
        user.full_name = full_name
    if is_active is not None:
        user.is_active = is_active
        if is_active is False:
            # Inline session revocation — keeps everything in ONE transaction.
            # revoke_user_sessions() from auth_service calls db.commit() internally,
            # which would split deactivation + revocation across two transactions.
            now = datetime.now(timezone.utc)
            await db.execute(
                sql_update(SessionModel)
                .where(
                    SessionModel.user_id == user_id,
                    SessionModel.organisation_id == org_id,
                    SessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=now, revoked_by=current_user_id, revoke_reason="admin_deactivation")
            )

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.user.update",
        resource_type="users",
        resource_id=user.id,
        user_id=current_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        before_data=before_data,
        after_data={"full_name": user.full_name, "is_active": user.is_active},
    )

    await db.commit()
    return await _build_user_response(db, user.id, org_id)


async def assign_role(
    db: AsyncSession,
    *,
    user_id: int,
    org_id: int,
    role: UserRoleEnum,
    assigning_user_id: int,
    ip_address: str | None,
) -> UserResponse:
    user = await _get_user_in_org(db, user_id=user_id, org_id=org_id)

    existing = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.organisation_id == org_id,
            UserRole.role == role,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role already assigned to this user")

    new_role = UserRole(
        user_id=user_id,
        organisation_id=org_id,
        role=role,
        assigned_by=assigning_user_id,
    )
    db.add(new_role)
    await db.flush()

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.user_role.assign",
        resource_type="user_roles",
        resource_id=new_role.id,
        user_id=assigning_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={"user_id": user_id, "role": role.value},
    )

    await db.commit()
    return await _build_user_response(db, user.id, org_id)
