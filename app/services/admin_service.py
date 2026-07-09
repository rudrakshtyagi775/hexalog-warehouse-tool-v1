from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func as sql_func
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import (
    AuditModuleEnum,
    CustomerStatusEnum,
    InwardBoxStatusEnum,
    OutwardPoStatusEnum,
    UserRoleEnum,
)
from app.models.inward import InwardBox, InwardPO, InwardScan
from app.models.organisation import Organisation
from app.models.outward import OutwardBox, OutwardPO, OutwardScan
from app.models.user import Session as UserSession
from app.models.user import User, UserOrganisation, UserRole
from app.schemas.admin import (
    AuditLogListResponse,
    AuditLogResponse,
    OrgResponse,
    OrgUpdateRequest,
    RecentSubmission,
    RecentSubmissionsResponse,
    StatsResponse,
    UserResponse,
    UserRoleResponse,
)
from app.services.audit_service import write_audit_log
from app.services.password_service import hash_password


async def get_stats(db: AsyncSession, *, organisation_id: int) -> StatsResponse:
    """Return dashboard summary counts for the organisation."""
    tz = ZoneInfo(settings.APP_TIMEZONE)
    from_dt = datetime.now(tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    from_dt_utc = from_dt.astimezone(UTC)

    boxes_this_month = await db.scalar(
        select(sql_func.count()).select_from(InwardBox).where(
            InwardBox.organisation_id == organisation_id,
            InwardBox.status == InwardBoxStatusEnum.completed,
            InwardBox.created_at >= from_dt_utc,
        )
    ) or 0

    total_scanned = await db.scalar(
        select(sql_func.count()).select_from(InwardScan).where(
            InwardScan.organisation_id == organisation_id,
            InwardScan.is_deleted == False,  # noqa: E712
        )
    ) or 0

    total_pos = await db.scalar(
        select(sql_func.count()).select_from(InwardPO).where(
            InwardPO.organisation_id == organisation_id,
        )
    ) or 0

    active_customers = await db.scalar(
        select(sql_func.count()).select_from(Customer).where(
            Customer.organisation_id == organisation_id,
            Customer.status == CustomerStatusEnum.active,
        )
    ) or 0

    outward_boxes_this_month = await db.scalar(
        select(sql_func.count()).select_from(OutwardBox).where(
            OutwardBox.organisation_id == organisation_id,
            OutwardBox.created_at >= from_dt_utc,
        )
    ) or 0

    outward_scans_this_month = await db.scalar(
        select(sql_func.count()).select_from(OutwardScan).where(
            OutwardScan.organisation_id == organisation_id,
            OutwardScan.created_at >= from_dt_utc,
            OutwardScan.scan_result != "deleted",
        )
    ) or 0

    open_po_count = await db.scalar(
        select(sql_func.count()).select_from(OutwardPO).where(
            OutwardPO.organisation_id == organisation_id,
            OutwardPO.status == OutwardPoStatusEnum.open,
        )
    ) or 0

    return StatsResponse(
        inward_boxes_completed_this_month=boxes_this_month,
        total_items_scanned=total_scanned,
        total_pos_uploaded=total_pos,
        active_customers=active_customers,
        outward_boxes_this_month=outward_boxes_this_month,
        outward_scans_this_month=outward_scans_this_month,
        open_po_count=open_po_count,
    )


async def get_recent_submissions(
    db: AsyncSession, *, organisation_id: int, limit: int = 10
) -> RecentSubmissionsResponse:
    """Return the most recently completed inward boxes with customer names."""
    result = await db.execute(
        select(InwardBox, Customer)
        .join(Customer, InwardBox.customer_id == Customer.id)
        .where(
            InwardBox.organisation_id == organisation_id,
            InwardBox.status == InwardBoxStatusEnum.completed,
            InwardBox.inscan_number != None,  # noqa: E711
        )
        .order_by(InwardBox.submitted_at.desc())
        .limit(limit)
    )
    rows = result.all()
    return RecentSubmissionsResponse(
        items=[
            RecentSubmission(
                inscan_number=box.inscan_number,
                customer_name=customer.name,
                box_id=box.box_id,
                scanned_qty=box.scanned_qty,
                submitted_at=box.submitted_at,
            )
            for box, customer in rows
        ]
    )


async def list_audit_logs(
    db: AsyncSession, *, organisation_id: int, page: int, page_size: int
) -> AuditLogListResponse:
    """Return a paginated list of audit log entries for the organisation."""
    total = await db.scalar(
        select(sql_func.count()).select_from(AuditLog).where(
            AuditLog.organisation_id == organisation_id,
        )
    ) or 0

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.organisation_id == organisation_id)
        .order_by(AuditLog.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    logs = result.scalars().all()

    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
    )


async def list_users(db: AsyncSession, *, organisation_id: int) -> list[UserResponse]:
    """Return all users who belong to the organisation, with their roles."""
    user_org_result = await db.execute(
        select(UserOrganisation.user_id).where(
            UserOrganisation.organisation_id == organisation_id
        )
    )
    user_ids = [r[0] for r in user_org_result.all()]
    if not user_ids:
        return []

    users_result = await db.execute(
        select(User).where(User.id.in_(user_ids)).order_by(User.created_at.desc())
    )
    users = users_result.scalars().all()

    roles_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id.in_(user_ids),
            UserRole.organisation_id == organisation_id,
        )
    )
    all_roles = roles_result.scalars().all()
    roles_by_user: dict[int, list[UserRole]] = {}
    for r in all_roles:
        roles_by_user.setdefault(r.user_id, []).append(r)

    return [
        UserResponse(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            is_active=u.is_active,
            roles=[
                UserRoleResponse(role=r.role, created_at=r.created_at)
                for r in roles_by_user.get(u.id, [])
            ],
            created_at=u.created_at,
        )
        for u in users
    ]


async def create_user(
    db: AsyncSession,
    *,
    organisation_id: int,
    created_by: int,
    ip_address: str | None,
    email: str,
    full_name: str,
    password: str,
    roles: list,
) -> UserResponse:
    """Create a new user, add them to the organisation, assign roles, and audit."""
    pw_hash = hash_password(password)

    user = User(email=email, full_name=full_name, password_hash=pw_hash, is_active=True)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    db.add(
        UserOrganisation(user_id=user.id, organisation_id=organisation_id, created_by=created_by)
    )

    for role in roles:
        db.add(
            UserRole(
                user_id=user.id,
                organisation_id=organisation_id,
                role=role,
                assigned_by=created_by,
            )
        )

    await db.flush()

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="user_created",
        resource_type="user",
        resource_id=user.id,
        user_id=created_by,
        organisation_id=organisation_id,
        after_data={"email": email, "full_name": full_name, "roles": [r.value for r in roles]},
        ip_address=ip_address,
    )

    await db.commit()

    # Reload to get server-generated timestamps
    reloaded_user = await db.get(User, user.id)
    roles_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.organisation_id == organisation_id,
        )
    )
    reloaded_roles = roles_result.scalars().all()

    return UserResponse(
        id=reloaded_user.id,
        email=reloaded_user.email,
        full_name=reloaded_user.full_name,
        is_active=reloaded_user.is_active,
        roles=[UserRoleResponse(role=r.role, created_at=r.created_at) for r in reloaded_roles],
        created_at=reloaded_user.created_at,
    )


# ── Private helpers ────────────────────────────────────────────────────────────


async def _get_user_in_org(db: AsyncSession, *, user_id: int, org_id: int) -> User:
    """Return user only if they belong to org_id. Raises 404 otherwise."""
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
    """Fetch user + org-scoped roles and assemble a UserResponse (no password_hash)."""
    user = await db.get(User, user_id)
    roles_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.organisation_id == org_id,
        )
    )
    org_roles = roles_result.scalars().all()
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=[UserRoleResponse(role=r.role, created_at=r.created_at) for r in org_roles],
        created_at=user.created_at,
    )


# ── User management ────────────────────────────────────────────────────────────


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
            now = datetime.now(tz=UTC)
            await db.execute(
                sql_update(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                )
                .values(
                    revoked_at=now,
                    revoked_by=current_user_id,
                    revoke_reason="user_deactivated",
                )
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role already assigned to this user",
        )

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


async def revoke_role(
    db: AsyncSession,
    *,
    user_id: int,
    org_id: int,
    role: UserRoleEnum,
    revoking_user_id: int,
    ip_address: str | None,
) -> UserResponse:
    user = await _get_user_in_org(db, user_id=user_id, org_id=org_id)

    if user_id == revoking_user_id and role == UserRoleEnum.admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin cannot revoke their own admin role",
        )

    result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.organisation_id == org_id,
            UserRole.role == role,
        )
    )
    role_row = result.scalar_one_or_none()
    if role_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not assigned to this user",
        )

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.user_role.revoke",
        resource_type="user_roles",
        resource_id=role_row.id,
        user_id=revoking_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        before_data={"user_id": user_id, "role": role.value},
    )

    await db.delete(role_row)
    await db.commit()
    return await _build_user_response(db, user.id, org_id)


async def update_organisation(
    db: AsyncSession,
    *,
    org_id: int,
    req: OrgUpdateRequest,
    admin_user_id: int,
    ip_address: str | None,
) -> OrgResponse:
    org = await db.get(Organisation, org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    if req.name is None and req.is_active is None:
        return OrgResponse.model_validate(org)

    before_data: dict = {}
    if req.name is not None:
        before_data["name"] = org.name
        org.name = req.name
    if req.is_active is not None:
        before_data["is_active"] = org.is_active
        org.is_active = req.is_active

    after_data = {}
    if req.name is not None:
        after_data["name"] = org.name
    if req.is_active is not None:
        after_data["is_active"] = org.is_active

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="organisation_updated",
        resource_type="organisations",
        resource_id=org.id,
        user_id=admin_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        before_data=before_data,
        after_data=after_data,
    )

    await db.commit()
    await db.refresh(org)
    return OrgResponse.model_validate(org)


async def delete_inward_box(
    db: AsyncSession,
    *,
    box_id: str,
    org_id: int,
    admin_user_id: int,
    ip_address: str | None,
) -> None:
    result = await db.execute(
        select(InwardBox).where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == org_id,
            InwardBox.is_deleted == False,  # noqa: E712
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    if box.status == InwardBoxStatusEnum.completed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot delete a completed box. Inscan Number already issued.",
        )

    box.is_deleted = True
    box.deleted_at = datetime.now(tz=UTC)
    box.deleted_by = admin_user_id

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="inward_box_deleted",
        resource_type="inward_boxes",
        resource_id=box.id,
        user_id=admin_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        before_data={"box_id": box_id, "status": box.status.value, "scanned_qty": box.scanned_qty},
        after_data={"is_deleted": True},
    )

    await db.commit()


async def admin_password_reset(
    db: AsyncSession,
    *,
    user_id: int,
    org_id: int,
    new_password: str,
    admin_user_id: int,
    ip_address: str | None,
) -> None:
    user = await _get_user_in_org(db, user_id=user_id, org_id=org_id)

    user.password_hash = hash_password(new_password)

    now = datetime.now(tz=UTC)
    await db.execute(
        sql_update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=admin_user_id, revoke_reason="password_reset")
    )

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.user.password_reset",
        resource_type="users",
        resource_id=user.id,
        user_id=admin_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={"user_id": user.id},
    )

    await db.commit()
