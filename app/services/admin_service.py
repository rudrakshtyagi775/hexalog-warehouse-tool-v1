from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func as sql_func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import AuditModuleEnum, CustomerStatusEnum, InwardBoxStatusEnum
from app.models.inward import InwardBox, InwardPO, InwardScan
from app.models.user import User, UserOrganisation, UserRole
from app.schemas.admin import (
    AuditLogListResponse,
    AuditLogResponse,
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

    return StatsResponse(
        inward_boxes_completed_this_month=boxes_this_month,
        total_items_scanned=total_scanned,
        total_pos_uploaded=total_pos,
        active_customers=active_customers,
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
