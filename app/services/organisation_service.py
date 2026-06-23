import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuditModuleEnum, UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole
from app.services.audit_service import write_audit_log

log = structlog.get_logger(__name__)


async def create_organisation(
    db: AsyncSession,
    *,
    name: str,
    creating_user_id: int,
    ip_address: str | None,
) -> Organisation:
    """Create a new org and bootstrap the creating admin as its first member."""
    org = Organisation(name=name, is_active=True)
    db.add(org)
    await db.flush()  # populate org.id before FK inserts

    db.add(UserOrganisation(
        user_id=creating_user_id,
        organisation_id=org.id,
        created_by=creating_user_id,
    ))
    db.add(UserRole(
        user_id=creating_user_id,
        organisation_id=org.id,
        role=UserRoleEnum.admin,
        assigned_by=creating_user_id,
    ))

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.organisation.create",
        resource_type="organisations",
        resource_id=org.id,
        user_id=creating_user_id,
        organisation_id=org.id,
        ip_address=ip_address,
        after_data={"name": name},
    )

    await db.commit()
    return org


async def get_organisation(
    db: AsyncSession,
    *,
    org_id: int,
) -> Organisation | None:
    # No membership check — callers must ensure org_id comes from a trusted source
    # (e.g. current_user.organisation_id from the JWT). Never pass a user-supplied
    # path/query parameter here without an explicit prior membership assertion.
    result = await db.execute(
        select(Organisation).where(Organisation.id == org_id)
    )
    return result.scalar_one_or_none()


async def list_user_organisations(
    db: AsyncSession,
    *,
    user_id: int,
) -> list[Organisation]:
    """Return all organisations the user belongs to, ordered by name."""
    result = await db.execute(
        select(Organisation)
        .join(UserOrganisation, UserOrganisation.organisation_id == Organisation.id)
        .where(UserOrganisation.user_id == user_id)
        .order_by(Organisation.name)
    )
    return list(result.scalars().all())


async def update_organisation(
    db: AsyncSession,
    *,
    org_id: int,
    name: str | None,
    is_active: bool | None,
    admin_user_id: int,
    ip_address: str | None,
) -> Organisation:
    org = await get_organisation(db, org_id=org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )

    if name is None and is_active is None:
        return org

    if is_active is False and org.is_active:
        # Refuse to deactivate while active users remain — prevents lockout.
        count_result = await db.execute(
            select(func.count(UserOrganisation.user_id))
            .join(User, User.id == UserOrganisation.user_id)
            .where(
                UserOrganisation.organisation_id == org_id,
                User.is_active.is_(True),
            )
        )
        if count_result.scalar_one() > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate an organisation with active users",
            )

    before_data = {"name": org.name, "is_active": org.is_active}

    if name is not None:
        org.name = name
    if is_active is not None:
        org.is_active = is_active

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="admin.organisation.update",
        resource_type="organisations",
        resource_id=org.id,
        user_id=admin_user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        before_data=before_data,
        after_data={"name": org.name, "is_active": org.is_active},
    )

    await db.commit()
    return org
