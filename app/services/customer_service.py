from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.enums import AuditModuleEnum, CustomerStatusEnum
from app.services.audit_service import write_audit_log


async def list_customers(
    db: AsyncSession,
    organisation_id: int,
    status: CustomerStatusEnum | None = None,
    q: str | None = None,
) -> list[Customer]:
    stmt = select(Customer).where(Customer.organisation_id == organisation_id)
    if status is not None:
        stmt = stmt.where(Customer.status == status)
    if q:
        stmt = stmt.where(Customer.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Customer.name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_customer(
    db: AsyncSession,
    customer_id: int,
    organisation_id: int,
) -> Customer | None:
    result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def create_customer(
    db: AsyncSession,
    *,
    name: str,
    code: str,
    organisation_id: int,
    created_by: int,
    ip_address: str | None,
) -> Customer:
    customer = Customer(
        name=name,
        code=code,
        organisation_id=organisation_id,
        created_by=created_by,
        status=CustomerStatusEnum.active,
    )
    db.add(customer)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Customer with code '{code}' already exists in this organisation.",
        )
    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="customer_created",
        resource_type="customer",
        resource_id=customer.id,
        user_id=created_by,
        organisation_id=organisation_id,
        before_data=None,
        after_data={"name": name, "code": code, "status": CustomerStatusEnum.active.value},
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(customer)
    return customer


async def update_customer(
    db: AsyncSession,
    *,
    customer_id: int,
    organisation_id: int,
    updated_by: int,
    ip_address: str | None,
    name: str | None,
    status: CustomerStatusEnum | None,
) -> Customer:
    customer = await get_customer(db, customer_id, organisation_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    if name is None and status is None:
        return customer

    before = {"name": customer.name, "status": customer.status.value}

    if name is not None:
        customer.name = name
    if status is not None:
        customer.status = status

    after = {"name": customer.name, "status": customer.status.value}

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="customer_updated",
        resource_type="customer",
        resource_id=customer.id,
        user_id=updated_by,
        organisation_id=organisation_id,
        before_data=before,
        after_data=after,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(customer)
    return customer
