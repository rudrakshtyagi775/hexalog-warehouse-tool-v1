from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user, require_admin
from app.models.enums import CustomerStatusEnum
from app.schemas.auth import CurrentUser
from app.schemas.customer import CustomerCreate, CustomerListItem, CustomerResponse
from app.services.customer_service import create_customer, get_customer, list_customers
from app.utils.request import get_client_ip

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


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer_endpoint(
    body: CustomerCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    return await create_customer(
        db,
        name=body.name,
        code=body.code,
        organisation_id=current_user.organisation_id,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
