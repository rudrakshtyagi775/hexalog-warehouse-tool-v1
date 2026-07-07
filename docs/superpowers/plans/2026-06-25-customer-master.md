# Customer Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Customer Master CRUD API — create, list, get, and update customers — with admin-only write access, multi-tenant read access for all roles (dropdown use), code immutability, and full audit logging.

**Architecture:** Follows the existing service → router → dependency layering. A new `customer_service.py` owns all DB queries and transactions; `routers/customer.py` is HTTP wiring only; schemas in `schemas/customer.py` enforce code format validation at the Pydantic layer. No new Alembic migration is required — the `customers` table was created in the initial migration `9014f72e9f0c`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest-asyncio, httpx.

## Global Constraints

- `organisation_id` always comes from the JWT (`current_user.organisation_id`) — never from the request body.
- Customer `code` must match `^[A-Z]{2,3}$` (2–3 uppercase letters; used in Box IDs and Inscan Numbers — immutable after creation).
- Inactive customers are hidden from new-selection dropdowns (UI concern enforced via `?status=active`); API itself does not restrict by status unless filtered.
- All write operations (create, update) are admin-only; read operations are accessible to any authenticated role.
- Every create and update must write an `AuditLog` row in the same transaction (`AuditModuleEnum.shared`).
- Append-only invariant: no hard-delete endpoint; deactivation is the only removal mechanism.
- `expire_on_commit=False` is set on the session — always call `await db.refresh(obj)` after commit if the response needs server-updated columns (e.g. `updated_at`).
- Ruff lint rules apply: `line-length=100`, `target-version=py312`.

---

## File Map

| Action | Path |
|--------|------|
| Create | `app/schemas/customer.py` |
| Create | `app/services/customer_service.py` |
| Create | `app/routers/customer.py` |
| Modify | `app/main.py` — include customer router |
| Create | `tests/unit/test_customer_schemas.py` |
| Create | `tests/integration/test_customers.py` |

---

## Task 1: Customer Schemas + Schema Validation Unit Tests

**Files:**
- Create: `app/schemas/customer.py`
- Create: `tests/unit/test_customer_schemas.py`

**Interfaces:**
- Produces: `CustomerCreate`, `CustomerUpdate`, `CustomerResponse`, `CustomerListItem` — consumed by Tasks 2–5.

- [ ] **Step 1: Write failing unit tests**

Create `tests/unit/test_customer_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.models.enums import CustomerStatusEnum


# ── CustomerCreate: code validation ──────────────────────────────────────────

def test_create_valid_2_letter_code():
    c = CustomerCreate(name="Nimai", code="NM")
    assert c.code == "NM"


def test_create_valid_3_letter_code():
    c = CustomerCreate(name="Kiran Enterprises", code="KIR")
    assert c.code == "KIR"


def test_create_code_too_short():
    with pytest.raises(ValidationError, match="2"):
        CustomerCreate(name="X", code="K")


def test_create_code_too_long():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="KIRN")


def test_create_code_lowercase():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="kir")


def test_create_code_mixed_case():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="KiR")


def test_create_code_with_digit():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="K1R")


def test_create_code_with_space():
    with pytest.raises(ValidationError):
        CustomerCreate(name="X", code="K R")


# ── CustomerCreate: name validation ──────────────────────────────────────────

def test_create_name_empty_string():
    with pytest.raises(ValidationError):
        CustomerCreate(name="", code="KIR")


def test_create_name_whitespace_only():
    with pytest.raises(ValidationError):
        CustomerCreate(name="   ", code="KIR")


def test_create_name_stripped():
    c = CustomerCreate(name="  Kiran  ", code="KIR")
    assert c.name == "Kiran"


# ── CustomerUpdate: field validation ─────────────────────────────────────────

def test_update_code_field_rejected():
    """code must not be patchable — extra="forbid" on CustomerUpdate."""
    with pytest.raises(ValidationError):
        CustomerUpdate.model_validate({"code": "KIR"})


def test_update_name_only():
    u = CustomerUpdate(name="New Name")
    assert u.name == "New Name"
    assert u.status is None


def test_update_status_only():
    u = CustomerUpdate(status=CustomerStatusEnum.inactive)
    assert u.status == CustomerStatusEnum.inactive
    assert u.name is None


def test_update_empty_name_rejected():
    with pytest.raises(ValidationError):
        CustomerUpdate(name="")


def test_update_whitespace_name_rejected():
    with pytest.raises(ValidationError):
        CustomerUpdate(name="   ")


def test_update_both_fields():
    u = CustomerUpdate(name="Renamed", status=CustomerStatusEnum.active)
    assert u.name == "Renamed"
    assert u.status == CustomerStatusEnum.active


def test_update_all_none_is_valid():
    u = CustomerUpdate()
    assert u.name is None
    assert u.status is None
```

- [ ] **Step 2: Run tests — expect ImportError / ModuleNotFoundError**

```
pytest tests/unit/test_customer_schemas.py -v
```

Expected: `ImportError: cannot import name 'CustomerCreate' from 'app.schemas.customer'`
(file doesn't exist yet)

- [ ] **Step 3: Implement `app/schemas/customer.py`**

```python
import re
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.enums import CustomerStatusEnum


class CustomerCreate(BaseModel):
    name: str
    code: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("code")
    @classmethod
    def code_format(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{2,3}$", v):
            raise ValueError("code must be 2–3 uppercase letters (A–Z only)")
        return v


class CustomerUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    status: CustomerStatusEnum | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("name must not be empty")
        return v


class CustomerResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    organisation_id: int
    name: str
    code: str
    status: CustomerStatusEnum
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class CustomerListItem(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    code: str
    status: CustomerStatusEnum
```

- [ ] **Step 4: Run tests — expect all 17 pass**

```
pytest tests/unit/test_customer_schemas.py -v
```

Expected: `17 passed`

- [ ] **Step 5: Commit**

```
git add app/schemas/customer.py tests/unit/test_customer_schemas.py
git commit -m "feat(customers): add customer schemas with code format validation"
```

---

## Task 2: list_customers() + GET /api/customers + Router + main.py Wiring

**Files:**
- Create: `app/services/customer_service.py`
- Create: `app/routers/customer.py`
- Modify: `app/main.py`
- Create (partial): `tests/integration/test_customers.py`

**Interfaces:**
- Consumes: `CustomerListItem` from Task 1; `get_current_user`, `require_admin` from `app/dependencies/auth.py`
- Produces: `list_customers(db, organisation_id, status?, q?)` — consumed by Task 3 onwards

- [ ] **Step 1: Write failing integration tests for the list endpoint**

Create `tests/integration/test_customers.py`:

```python
import pytest
import pytest_asyncio

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum
from app.models.organisation import Organisation


BASE = "/api/customers"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def admin_token(client, admin_user, org):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def packer_token(client, packer_user, org):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "packer@test.com", "password": "PackerPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def active_customer(db, org, admin_user):
    c = Customer(
        name="Kiran Enterprises",
        code="KIR",
        organisation_id=org.id,
        created_by=admin_user.id,
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


@pytest_asyncio.fixture
async def inactive_customer(db, org, admin_user):
    c = Customer(
        name="Inactive Corp",
        code="INC",
        organisation_id=org.id,
        created_by=admin_user.id,
        status=CustomerStatusEnum.inactive,
    )
    db.add(c)
    await db.flush()
    return c


@pytest_asyncio.fixture
async def other_org(db):
    o = Organisation(name="Other Org", is_active=True)
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture
async def other_org_customer(db, other_org):
    c = Customer(
        name="Foreign Customer",
        code="FOR",
        organisation_id=other_org.id,
        created_by=None,
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


# ── List tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_customers_returns_own_org_only(
    client, admin_token, active_customer, other_org_customer
):
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert active_customer.id in ids
    assert other_org_customer.id not in ids


@pytest.mark.asyncio
async def test_list_customers_includes_inactive_by_default(
    client, admin_token, active_customer, inactive_customer
):
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert active_customer.id in ids
    assert inactive_customer.id in ids


@pytest.mark.asyncio
async def test_list_customers_filter_active_only(
    client, admin_token, active_customer, inactive_customer
):
    resp = await client.get(
        f"{BASE}?status=active", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert active_customer.id in ids
    assert inactive_customer.id not in ids


@pytest.mark.asyncio
async def test_list_customers_filter_inactive_only(
    client, admin_token, active_customer, inactive_customer
):
    resp = await client.get(
        f"{BASE}?status=inactive", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert inactive_customer.id in ids
    assert active_customer.id not in ids


@pytest.mark.asyncio
async def test_list_customers_name_search(client, admin_token, active_customer):
    resp = await client.get(
        f"{BASE}?q=kiran", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert any(c["id"] == active_customer.id for c in resp.json())


@pytest.mark.asyncio
async def test_list_customers_name_search_no_match(client, admin_token, active_customer):
    resp = await client.get(
        f"{BASE}?q=zzznomatch", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_customers_packer_allowed(client, packer_token, active_customer):
    """All authenticated roles can list customers (needed for dropdowns)."""
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {packer_token}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_customers_unauthenticated(client):
    resp = await client.get(BASE)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_customers_sorted_by_name(client, admin_token, db, org, admin_user):
    for code, name in [("BBB", "Zara Corp"), ("AAA", "Alpha Inc")]:
        db.add(Customer(
            name=name, code=code, organisation_id=org.id,
            created_by=admin_user.id, status=CustomerStatusEnum.active,
        ))
    await db.flush()
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert names == sorted(names)
```

- [ ] **Step 2: Run tests — expect 404 (route not registered)**

```
pytest tests/integration/test_customers.py -k "list" -v
```

Expected: Tests fail with `assert 404 == 200` (router not yet in main.py)

- [ ] **Step 3: Create `app/services/customer_service.py` with `list_customers`**

```python
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
```

- [ ] **Step 4: Create `app/routers/customer.py` with GET /api/customers**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.enums import CustomerStatusEnum
from app.schemas.auth import CurrentUser
from app.schemas.customer import CustomerListItem
from app.services.customer_service import list_customers

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
```

- [ ] **Step 5: Wire the router in `app/main.py`**

Add this import (after the existing router imports):

```python
from app.routers import customer as customer_router
```

Add this line inside `create_app()` after the existing `include_router` calls:

```python
app.include_router(customer_router.router)
```

The `create_app()` body becomes:

```python
app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(customer_router.router)
```

- [ ] **Step 6: Run list tests — expect all 9 pass**

```
pytest tests/integration/test_customers.py -k "list" -v
```

Expected: `9 passed`

- [ ] **Step 7: Commit**

```
git add app/schemas/customer.py app/services/customer_service.py \
        app/routers/customer.py app/main.py \
        tests/integration/test_customers.py
git commit -m "feat(customers): add list customers endpoint with org isolation and name search"
```

---

## Task 3: get_customer() + GET /api/customers/{id}

**Files:**
- Modify: `app/services/customer_service.py` — add `get_customer()`
- Modify: `app/routers/customer.py` — add GET `/{id}` route
- Modify: `tests/integration/test_customers.py` — append get-by-ID tests

**Interfaces:**
- Produces: `get_customer(db, customer_id, organisation_id) -> Customer | None` — consumed by Task 5 (update).

- [ ] **Step 1: Append failing tests for GET /{id} to the test file**

Add to the bottom of `tests/integration/test_customers.py`:

```python
# ── Get by ID tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_customer_by_id(client, admin_token, active_customer):
    resp = await client.get(
        f"{BASE}/{active_customer.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == active_customer.id
    assert data["code"] == "KIR"
    assert data["name"] == "Kiran Enterprises"
    assert data["status"] == "active"
    assert "organisation_id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_get_customer_not_found(client, admin_token):
    resp = await client.get(
        f"{BASE}/999999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_customer_wrong_org_returns_404(client, admin_token, other_org_customer):
    """Cross-org lookup must 404 — never reveal another org's data."""
    resp = await client.get(
        f"{BASE}/{other_org_customer.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_customer_packer_allowed(client, packer_token, active_customer):
    resp = await client.get(
        f"{BASE}/{active_customer.id}",
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_customer_unauthenticated(client, active_customer):
    resp = await client.get(f"{BASE}/{active_customer.id}")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run get-by-ID tests — expect 404 (route not registered yet)**

```
pytest tests/integration/test_customers.py -k "get_customer" -v
```

Expected: `assert 404 == 200` / `assert 404 == 404` (route missing, falls through to FastAPI 404)

- [ ] **Step 3: Add `get_customer()` to `app/services/customer_service.py`**

Add after the `list_customers` function:

```python
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
```

- [ ] **Step 4: Add GET `/{id}` route to `app/routers/customer.py`**

Add these imports at the top:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.schemas.customer import CustomerListItem, CustomerResponse
from app.services.customer_service import get_customer, list_customers
```

Add the route handler after the list endpoint:

```python
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
```

- [ ] **Step 5: Run get-by-ID tests — expect all 5 pass**

```
pytest tests/integration/test_customers.py -k "get_customer" -v
```

Expected: `5 passed`

- [ ] **Step 6: Run full suite to check no regressions**

```
pytest tests/integration/test_customers.py -v
```

Expected: `14 passed` (9 list + 5 get)

- [ ] **Step 7: Commit**

```
git add app/services/customer_service.py app/routers/customer.py \
        tests/integration/test_customers.py
git commit -m "feat(customers): add get customer by ID endpoint"
```

---

## Task 4: create_customer() + POST /api/customers

**Files:**
- Modify: `app/services/customer_service.py` — add `create_customer()`
- Modify: `app/routers/customer.py` — add POST route
- Modify: `tests/integration/test_customers.py` — append create tests

**Interfaces:**
- Consumes: `CustomerCreate` from Task 1; `write_audit_log` from `app/services/audit_service`
- Produces: `create_customer(db, name, code, organisation_id, created_by, ip_address) -> Customer`

- [ ] **Step 1: Append failing create tests**

Add to the bottom of `tests/integration/test_customers.py`:

```python
# ── Create tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_customer_admin_success(client, admin_token, org):
    resp = await client.post(
        BASE,
        json={"name": "New Customer", "code": "NEW"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "New Customer"
    assert data["code"] == "NEW"
    assert data["status"] == "active"
    assert data["organisation_id"] == org.id
    assert data["id"] > 0
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_customer_packer_forbidden(client, packer_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "TST"},
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_customer_unauthenticated(client):
    resp = await client.post(BASE, json={"name": "Test", "code": "TST"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_customer_duplicate_code(client, admin_token, active_customer):
    resp = await client.post(
        BASE,
        json={"name": "Another Kiran", "code": "KIR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409
    assert "KIR" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_customer_invalid_code_lowercase(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "kir"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_code_too_short(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "K"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_code_too_long(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "KIRN"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_empty_name(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "", "code": "KIR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_duplicate_code_other_org_ok(
    client, admin_token, other_org_customer
):
    """Same code in a different org is allowed — codes are unique per org."""
    resp = await client.post(
        BASE,
        json={"name": "Foreign Clone", "code": "FOR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_customer_audit_log_written(client, admin_token, db):
    from app.models.audit_log import AuditLog
    from sqlalchemy import select as sa_select
    resp = await client.post(
        BASE,
        json={"name": "Audit Test", "code": "AUD"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    customer_id = resp.json()["id"]
    result = await db.execute(
        sa_select(AuditLog).where(
            AuditLog.resource_type == "customer",
            AuditLog.resource_id == customer_id,
            AuditLog.action == "customer_created",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.after_data["code"] == "AUD"
```

- [ ] **Step 2: Run create tests — expect 404 (POST route missing)**

```
pytest tests/integration/test_customers.py -k "create" -v
```

Expected: Most fail with `assert 404 == 201` or `assert 404 == 403`

- [ ] **Step 3: Add `create_customer()` to `app/services/customer_service.py`**

Add this import at the top of the file:

```python
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
```

Add the function after `get_customer`:

```python
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
        after_data={"name": name, "code": code, "status": CustomerStatusEnum.active.value},
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(customer)
    return customer
```

- [ ] **Step 4: Add POST route to `app/routers/customer.py`**

Add these imports at the top:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from app.dependencies.auth import get_current_user, require_admin
from app.schemas.customer import CustomerCreate, CustomerListItem, CustomerResponse
from app.services.customer_service import create_customer, get_customer, list_customers
from app.utils.request import get_client_ip
```

Add the POST handler after the GET `/{id}` handler:

```python
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
```

- [ ] **Step 5: Run create tests — expect all 10 pass**

```
pytest tests/integration/test_customers.py -k "create" -v
```

Expected: `10 passed`

- [ ] **Step 6: Run full suite — expect 24 pass**

```
pytest tests/integration/test_customers.py -v
```

Expected: `24 passed`

- [ ] **Step 7: Commit**

```
git add app/services/customer_service.py app/routers/customer.py \
        tests/integration/test_customers.py
git commit -m "feat(customers): add create customer endpoint with audit logging"
```

---

## Task 5: update_customer() + PATCH /api/customers/{id}

**Files:**
- Modify: `app/services/customer_service.py` — add `update_customer()`
- Modify: `app/routers/customer.py` — add PATCH route
- Modify: `tests/integration/test_customers.py` — append update tests

**Interfaces:**
- Consumes: `get_customer()` from Task 3; `CustomerUpdate` from Task 1
- Produces: `update_customer(db, customer_id, organisation_id, updated_by, ip_address, name, status) -> Customer`

- [ ] **Step 1: Append failing update tests**

Add to the bottom of `tests/integration/test_customers.py`:

```python
# ── Update tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_customer_name(client, admin_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "Renamed Customer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Renamed Customer"
    assert data["code"] == "KIR"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_deactivate_customer(client, admin_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"status": "inactive"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_reactivate_customer(client, admin_token, inactive_customer):
    resp = await client.patch(
        f"{BASE}/{inactive_customer.id}",
        json={"status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_update_customer_both_fields(client, admin_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "New Name", "status": "inactive"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Name"
    assert data["status"] == "inactive"


@pytest.mark.asyncio
async def test_update_customer_code_rejected(client, admin_token, active_customer):
    """code is immutable — any attempt to patch it must return 422."""
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"code": "NEW"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_customer_packer_forbidden(client, packer_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "Hijack"},
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_customer_unauthenticated(client, active_customer):
    resp = await client.patch(f"{BASE}/{active_customer.id}", json={"name": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_customer_not_found(client, admin_token):
    resp = await client.patch(
        f"{BASE}/999999",
        json={"name": "Ghost"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_customer_wrong_org_returns_404(
    client, admin_token, other_org_customer
):
    resp = await client.patch(
        f"{BASE}/{other_org_customer.id}",
        json={"name": "Steal"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_customer_no_op_returns_200(client, admin_token, active_customer):
    """Empty body (no fields to change) should return 200 unchanged."""
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "KIR"


@pytest.mark.asyncio
async def test_update_customer_audit_log_written(client, admin_token, active_customer, db):
    from app.models.audit_log import AuditLog
    from sqlalchemy import select as sa_select
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "Audit Changed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    result = await db.execute(
        sa_select(AuditLog).where(
            AuditLog.resource_type == "customer",
            AuditLog.resource_id == active_customer.id,
            AuditLog.action == "customer_updated",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.before_data["name"] == "Kiran Enterprises"
    assert log.after_data["name"] == "Audit Changed"
```

- [ ] **Step 2: Run update tests — expect 405 or 404 (PATCH route missing)**

```
pytest tests/integration/test_customers.py -k "update or deactivate or reactivate" -v
```

Expected: Tests fail with `assert 405 == 200` or `assert 404 == 200`

- [ ] **Step 3: Add `update_customer()` to `app/services/customer_service.py`**

Add after `create_customer`:

```python
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
        raise HTTPException(
            status_code=status_code_404_not_found(),
            detail="Customer not found",
        )
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
```

**Note:** `status_code_404_not_found()` is a placeholder — use the actual HTTPException. Replace the function body's raise line with:

```python
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
```

The full `update_customer` function (corrected):

```python
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
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
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
```

Add this import at the top of `customer_service.py`:

```python
from http import HTTPStatus

HTTP_404_NOT_FOUND = 404
```

Or simpler — just use the integer `404` directly since `fastapi.status` is already imported via HTTPException. The cleanest approach:

Add to imports in `customer_service.py`:
```python
from fastapi import HTTPException
```

And use `status_code=404` directly in the raise.

- [ ] **Step 4: Add PATCH route to `app/routers/customer.py`**

Update the import line for `customer_service` imports:

```python
from app.services.customer_service import create_customer, get_customer, list_customers, update_customer
```

Add the PATCH handler:

```python
@router.patch("/{customer_id}", response_model=CustomerResponse)
async def update_customer_endpoint(
    customer_id: int,
    body: CustomerUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    return await update_customer(
        db,
        customer_id=customer_id,
        organisation_id=current_user.organisation_id,
        updated_by=current_user.user_id,
        ip_address=get_client_ip(request),
        name=body.name,
        status=body.status,
    )
```

Add `CustomerUpdate` to the schema imports:

```python
from app.schemas.customer import CustomerCreate, CustomerListItem, CustomerResponse, CustomerUpdate
```

- [ ] **Step 5: Run update tests — expect all 11 pass**

```
pytest tests/integration/test_customers.py -k "update or deactivate or reactivate" -v
```

Expected: `11 passed`

- [ ] **Step 6: Run the full customer test suite**

```
pytest tests/integration/test_customers.py -v
```

Expected: `35 passed`

- [ ] **Step 7: Run all tests to catch any regressions**

```
pytest tests/ -v
```

Expected: All prior tests (80 passing from auth module) + 35 new = `115 passed` (adjust if count differs)

- [ ] **Step 8: Run linter**

```
ruff check app/schemas/customer.py app/services/customer_service.py app/routers/customer.py
```

Expected: No errors

- [ ] **Step 9: Commit**

```
git add app/services/customer_service.py app/routers/customer.py \
        tests/integration/test_customers.py
git commit -m "feat(customers): add update customer endpoint with audit logging"
```

---

## Self-Review

### Spec Coverage

| PRD Requirement | Task | Status |
|----------------|------|--------|
| SH-4: name, code (2–3 uppercase), status (Active/Inactive) | Task 1 | ✅ |
| SH-4: code unique per org | Task 4 (409 on duplicate) | ✅ |
| §4.2: Admin manages customers | Tasks 2–5 (require_admin on write routes) | ✅ |
| §4.2: Inward Op + Packer read (for dropdowns) | Task 2 (`get_current_user` on list/get) | ✅ |
| §4.1: Inactive customers hidden from dropdowns | Task 2 (`?status=active` query param) | ✅ |
| §5.2: Standard columns (id, created_at, updated_at, created_by, org_id) | Task 1 (CustomerResponse) | ✅ |
| §6.1: Searchable dropdown — implies name search | Task 2 (`?q=` param with ilike) | ✅ |
| §10: Audit customer master changes | Tasks 4 + 5 (write_audit_log in both) | ✅ |
| §5.4: Code used in Box IDs and Inscan Numbers → immutable | Task 1 (code not in CustomerUpdate) | ✅ |
| Multi-tenancy: org_id from JWT always | All tasks (organisation_id = current_user.organisation_id) | ✅ |
| No hard-delete (append-only data model) | No DELETE endpoint defined | ✅ |

### Placeholder Scan

No "TBD" or "TODO" items remain. All code is complete.

### Type Consistency

- `list_customers` returns `list[Customer]` (SQLAlchemy ORM); router uses `response_model=list[CustomerListItem]` with `from_attributes=True` — FastAPI converts automatically.
- `get_customer` returns `Customer | None` — router raises 404 on None.
- `create_customer` returns `Customer` — router uses `response_model=CustomerResponse` with `from_attributes=True`.
- `update_customer` returns `Customer` — same response_model.
- `CustomerUpdate.status` is typed as `CustomerStatusEnum | None` and `update_customer` parameter `status: CustomerStatusEnum | None` — names and types match.

### Architectural Issues Found & Fixed

1. **`update_customer` uses `status` both as a parameter name and `fastapi.status` module** — the parameter `status: CustomerStatusEnum | None` would shadow the imported `fastapi.status`. Fixed by using the integer `404` directly in the raise, without importing `fastapi.status` inside the service. The service layer only needs `HTTPException`, not the full `fastapi.status` namespace.

2. **`db.refresh()` after update** — with `expire_on_commit=False`, `updated_at` (a server-side timestamp) won't reflect the new value after commit without an explicit refresh. Fixed: `await db.refresh(customer)` is called in both `create_customer` and `update_customer`.

3. **`IntegrityError` rollback in `create_customer`** — after catching `IntegrityError` from `flush()`, `rollback()` is called before raising HTTPException. This is correct: the caller's session is now clean and subsequent requests on the same `db` dependency won't see a broken transaction.

4. **`other_org_customer` fixture uses `created_by=None`** — the `customers.created_by` FK allows `SET NULL` so this is valid.

---

## Risks & PRD Ambiguities to Clarify Before Implementation

| # | Issue | Impact | Recommendation |
|---|-------|--------|----------------|
| R-1 | PRD says "2–3 uppercase letters" for code but doesn't specify if mixed-case input should be rejected or auto-uppercased | Low | Plan validates strictly (rejects lowercase) — confirm with Arpit/Adesh whether operators expect auto-uppercasing |
| R-2 | PRD is silent on whether non-admin roles can see inactive customers via the API (vs just via dropdowns showing only active) | Low | Plan allows any authenticated user to query any status — frontend enforces active-only for dropdowns |
| R-3 | PRD doesn't specify search parameters for customer lookup beyond "searchable dropdown" | Low | Plan adds `?q=` name search — confirm if code search is also needed |
| R-4 | Concurrent create with same code: DB unique constraint handles it, but the error surface is a generic IntegrityError that may also be triggered by other constraint violations in future | Low | Current handling is safe for v1 — revisit if more constraints are added |
| R-5 | No pagination specified — customer master is likely small per org (tens to low hundreds) | Low | Implement without pagination; revisit if a single org has >500 customers |
