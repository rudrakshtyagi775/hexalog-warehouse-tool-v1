from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.enums import CustomerStatusEnum
from app.schemas.auth import CurrentUser
from app.schemas.customer import CustomerListItem, CustomerResponse
from app.services.customer_service import get_customer, list_customers

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("", response_model=list[CustomerListItem])
async def list_customers_endpoint(
    status: CustomerStatusEnum | None = Query(default=None),
    q: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CustomerListItem]:
    return await list_customers(
        db,
        organisation_id=current_user.organisation_id,
        status=status,
        q=q,
    )


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer_endpoint(
    customer_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    customer = await get_customer(db, customer_id, current_user.organisation_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    return customer
