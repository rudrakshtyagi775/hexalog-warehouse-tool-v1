from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum


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
