# User Management & RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the six admin-only User Management & RBAC endpoints scoped to the active organisation, with session revocation, audit logging, and full integration test coverage.

**Architecture:** A dedicated `user_service.py` handles all business logic (org-scoping, uniqueness checks, business-rule guards, session invalidation, audit writes, and DB commits). The six new endpoints are appended to the existing `app/routers/admin.py`. Pydantic schemas live in a new `app/schemas/user.py`. Services return pre-built `UserResponse` Pydantic objects to guarantee `password_hash` can never leak through ORM serialisation.

**Tech Stack:** FastAPI · Async SQLAlchemy 2.0 (asyncpg) · Pydantic v2 · passlib bcrypt · pytest-asyncio integration tests · PostgreSQL 16

---

## Global Constraints

- `password_hash` must never appear in any API response, audit log `after_data`/`before_data`, structlog output, or Pydantic schema field — this is a hard security invariant.
- All user queries must be scoped to `current_user.organisation_id` (from JWT) via a `user_organisations` join. Never trust a user-supplied org id.
- Audit log entry written inside the transaction, before `db.commit()`, for every state-changing operation. Follow exact pattern from `app/services/organisation_service.py`.
- Admin cannot deactivate themselves (400).
- Admin cannot revoke their own `admin` role (400).
- Deactivating a user must set `user.tokens_invalidated_at = datetime.now(timezone.utc)` in the **same transaction** as `user.is_active = False`.
- Email uniqueness is system-wide (not org-scoped). `POST /api/admin/users` returns 409 if the email already exists.
- Hard-delete `user_roles` row on role revocation — the only hard-delete in the system; audit log records it.
- Role path parameter (`DELETE /api/admin/users/{id}/roles/{role}`) is validated by FastAPI as `UserRoleEnum`; invalid values return 422.
- Tests follow the exact style of `tests/integration/test_organisation_management.py` — no `@pytest.mark.asyncio` decorator (configured globally), `_login` helper reused, `_create_user_in_org` helper for direct DB setup.
- All endpoints require `require_admin` dependency.

---

## Pre-flight: foundational files

> The six files listed below are **already implemented in the `develop` branch** (auth module). This plan assumes they are available in the working tree. If `feature/user-management-rbac` has not been rebased onto `develop` yet, do that first before Task 1.

| File | What it provides |
|------|-----------------|
| `app/main.py` | FastAPI app, router registration |
| `app/database.py` | `get_db` async session factory |
| `app/models/user.py` | `User`, `UserOrganisation`, `UserRole` ORM models |
| `app/models/enums.py` | `UserRoleEnum`, `AuditModuleEnum` |
| `app/models/audit_log.py` | `AuditLog` ORM model |
| `app/dependencies/auth.py` | `CurrentUser`, `require_admin` |
| `app/services/auth_service.py` | `revoke_session`, `revoke_user_sessions` |
| `app/services/audit_service.py` | `write_audit_log(db, *, module, action, resource_type, resource_id, user_id, organisation_id, ip_address, before_data, after_data)` |
| `app/schemas/common.py` | `MessageResponse` |
| `app/utils/request.py` | `get_client_ip(request) -> str \| None` |
| `tests/conftest.py` | `client`, `admin_user`, `packer_user`, `org`, `db` fixtures |

---

## File Map

| Status | File | Responsibility |
|--------|------|---------------|
| **Create** | `app/schemas/user.py` | Pydantic request/response schemas for all user endpoints |
| **Create** | `app/services/user_service.py` | All user management business logic + DB operations |
| **Modify** | `app/routers/admin.py` | Append 6 new endpoint handlers (keep org routes untouched) |
| **Create** | `tests/integration/test_user_management.py` | Full integration test suite (36 tests) |

---

## Task 1: User Schemas

**Files:**
- Create: `app/schemas/user.py`

**Interfaces:**
- Produces:
  - `UserRoleInfo(role: UserRoleEnum, created_at: datetime)` — embeds in UserResponse
  - `UserResponse(id, email, full_name, is_active, created_at, roles: list[UserRoleInfo])` — returned by all user endpoints
  - `CreateUserRequest(email: EmailStr, full_name: str[1–200], password: str[8–128], roles: list[UserRoleEnum])` — POST /users body
  - `UpdateUserRequest(full_name: str | None, is_active: bool | None)` — PATCH /users/{id} body
  - `AssignRoleRequest(role: UserRoleEnum)` — POST /users/{id}/roles body
  - `AdminPasswordResetRequest(new_password: str[8–128])` — POST /users/{id}/password-reset body

- [ ] **Step 1: Create `app/schemas/user.py`**

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRoleEnum


class UserRoleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: UserRoleEnum
    created_at: datetime


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool
    created_at: datetime
    roles: list[UserRoleInfo]


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    roles: list[UserRoleEnum] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class AssignRoleRequest(BaseModel):
    role: UserRoleEnum


class AdminPasswordResetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    new_password: str = Field(min_length=8, max_length=128)
```

- [ ] **Step 2: Verify schemas import cleanly**

```bash
python -c "from app.schemas.user import UserResponse, CreateUserRequest, UpdateUserRequest, AssignRoleRequest, AdminPasswordResetRequest; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/schemas/user.py
git commit -m "feat(user): add user management Pydantic schemas"
```

---

## Task 2: Service Foundation + GET /api/admin/users

**Files:**
- Create: `app/services/user_service.py`
- Modify: `app/routers/admin.py` (append GET /users)
- Create: `tests/integration/test_user_management.py` (GET section only)

**Interfaces:**
- Consumes: `write_audit_log` from `app.services.audit_service`, `User`, `UserOrganisation`, `UserRole` from `app.models.user`, `UserRoleEnum`, `AuditModuleEnum` from `app.models.enums`
- Produces: `list_org_users(db, *, org_id: int) -> list[UserResponse]`; `_get_user_in_org(db, *, user_id, org_id) -> User` (raises 404)

- [ ] **Step 1: Write failing tests for GET /api/admin/users**

```python
# tests/integration/test_user_management.py
"""Integration tests for User Management & RBAC endpoints.

Endpoints under test:
  GET    /api/admin/users                   — list org users (admin only)
  POST   /api/admin/users                   — create user (admin only)
  PATCH  /api/admin/users/{id}              — update user (admin only)
  POST   /api/admin/users/{id}/roles        — assign role (admin only)
  DELETE /api/admin/users/{id}/roles/{role} — revoke role (admin only)
  POST   /api/admin/users/{id}/password-reset — admin password reset (admin only)

Invariants verified:
  - All queries scoped to current_user.organisation_id
  - password_hash never appears in any response
  - Audit log written in the same transaction as every state change
  - Admin cannot deactivate themselves
  - Admin cannot revoke their own admin role
  - Deactivating a user sets tokens_invalidated_at in the same transaction
  - Email uniqueness is system-wide
"""

from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

LOGIN_URL = "/api/auth/login"
USERS_URL = "/api/admin/users"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _login(client, user, org, password: str) -> str:
    resp = await client.post(
        LOGIN_URL,
        json={"email": user.email, "password": password, "organisation_id": org.id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"]


async def _create_user_in_org(db, *, org, email: str, password: str, roles=None, full_name="Test User"):
    """Insert a user directly via DB — bypasses the API for test setup."""
    user = User(
        email=email,
        full_name=full_name,
        password_hash=_pwd_ctx.hash(password),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(UserOrganisation(user_id=user.id, organisation_id=org.id, created_by=user.id))
    for role in (roles or []):
        db.add(UserRole(user_id=user.id, organisation_id=org.id, role=role, assigned_by=user.id))
    await db.commit()
    await db.refresh(user)
    return user


# ── GET /api/admin/users ──────────────────────────────────────────────────────

async def test_list_users_returns_org_users(client, admin_user, org, db):
    """GET /users returns all users belonging to the admin's organisation."""
    target = await _create_user_in_org(
        db, org=org, email="operator@test.com", password="Operator1!",
        roles=[UserRoleEnum.inward_operator],
    )

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()]
    assert admin_user.id in ids
    assert target.id in ids


async def test_list_users_excludes_other_org_users(client, admin_user, org, db):
    """Users in a different org must not appear in the response."""
    other_org = Organisation(name="Other Org", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(
        email="outsider@test.com", full_name="Outsider",
        password_hash=_pwd_ctx.hash("Outside1!"), is_active=True,
    )
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.commit()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()]
    assert outsider.id not in ids


async def test_list_users_response_excludes_password_hash(client, admin_user, org, db):
    """No user object in the list may contain password_hash."""
    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    for user in resp.json():
        assert "password_hash" not in user


async def test_list_users_non_admin_returns_403(client, packer_user, org, db):
    token = await _login(client, packer_user, org, "PackerPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_list_users_no_auth_returns_401(client):
    resp = await client.get(USERS_URL)
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_user_management.py::test_list_users_returns_org_users -v
```

Expected: FAIL — `404 Not Found` or `ImportError` (endpoint does not exist yet)

- [ ] **Step 3: Create `app/services/user_service.py` with list function**

```python
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuditModuleEnum, UserRoleEnum
from app.models.user import User, UserOrganisation, UserRole
from app.schemas.user import UserResponse, UserRoleInfo
from app.services.audit_service import write_audit_log

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return _pwd_ctx.hash(password)


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


async def list_org_users(db: AsyncSession, *, org_id: int) -> list[UserResponse]:
    """Return all users in the org with their org-scoped roles, ordered by name."""
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
```

- [ ] **Step 4: Add GET /users endpoint to `app/routers/admin.py`**

Add these imports at the top of the existing imports block:

```python
from app.models.enums import UserRoleEnum
from app.schemas.user import (
    AdminPasswordResetRequest,
    AssignRoleRequest,
    CreateUserRequest,
    UpdateUserRequest,
    UserResponse,
)
from app.services.user_service import (
    admin_password_reset,
    assign_role,
    create_user,
    list_org_users,
    revoke_role,
    update_user,
)
```

Append this endpoint after the existing organisation endpoints:

```python
# ── User management ───────────────────────────────────────────────────────────

@router.get(
    "/users",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
)
async def list_users_endpoint(
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """List all users in the admin's current organisation."""
    return await list_org_users(db, org_id=current_user.organisation_id)
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/integration/test_user_management.py -k "list_users" -v
```

Expected: 5 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/user_service.py app/routers/admin.py tests/integration/test_user_management.py
git commit -m "feat(user): add GET /api/admin/users endpoint with org-scoped list"
```

---

## Task 3: POST /api/admin/users (Create User)

**Files:**
- Modify: `app/services/user_service.py` (add `create_user`)
- Modify: `app/routers/admin.py` (append POST /users)
- Modify: `tests/integration/test_user_management.py` (append POST section)

**Interfaces:**
- Produces: `create_user(db, *, email, full_name, password, roles, org_id, creating_user_id, ip_address) -> UserResponse`

- [ ] **Step 1: Append failing POST /users tests to `tests/integration/test_user_management.py`**

```python
# ── POST /api/admin/users ─────────────────────────────────────────────────────

async def test_create_user_success(client, admin_user, org, db):
    """Admin can create a new user; response has correct fields."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "new@test.com", "full_name": "New User", "password": "NewPass1!", "roles": ["packer"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "new@test.com"
    assert data["full_name"] == "New User"
    assert data["is_active"] is True
    assert any(r["role"] == "packer" for r in data["roles"])
    assert "id" in data
    assert "created_at" in data


async def test_create_user_response_excludes_password_hash(client, admin_user, org, db):
    """Created user response must not expose password_hash."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "secure@test.com", "full_name": "Secure", "password": "SecurePass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    assert "password_hash" not in resp.json()


async def test_create_user_duplicate_email_returns_409(client, admin_user, org, db):
    """Creating a user with an already-registered email returns 409."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    await client.post(
        USERS_URL,
        json={"email": "dup@test.com", "full_name": "First", "password": "FirstPass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        USERS_URL,
        json={"email": "dup@test.com", "full_name": "Second", "password": "SecondPass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 409


async def test_create_user_writes_audit_log(client, admin_user, org, db):
    """POST /users writes an audit_logs row with action admin.user.create."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "audited@test.com", "full_name": "Audited", "password": "AuditPass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    new_user_id = resp.json()["id"]

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user.create",
            AuditLog.resource_id == new_user_id,
            AuditLog.user_id == admin_user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "users"
    assert audit.after_data["email"] == "audited@test.com"
    assert "password_hash" not in (audit.after_data or {})
    assert "password" not in (audit.after_data or {})


async def test_create_user_no_roles_succeeds(client, admin_user, org, db):
    """User can be created without any roles (empty roles list is valid)."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "norole@test.com", "full_name": "No Role", "password": "NoRolePass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    assert resp.json()["roles"] == []


async def test_create_user_non_admin_returns_403(client, packer_user, org, db):
    token = await _login(client, packer_user, org, "PackerPass1!")
    resp = await client.post(
        USERS_URL,
        json={"email": "x@test.com", "full_name": "X", "password": "XPassword1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_create_user_no_auth_returns_401(client):
    resp = await client.post(USERS_URL, json={"email": "x@test.com", "full_name": "X", "password": "XPassword1!"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_user_management.py -k "create_user" -v
```

Expected: FAIL — `404 Not Found` (endpoint not implemented)

- [ ] **Step 3: Add `create_user` to `app/services/user_service.py`**

```python
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
```

- [ ] **Step 4: Append POST /users endpoint to `app/routers/admin.py`**

```python
@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_endpoint(
    body: CreateUserRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Create a new user and add them to the admin's current organisation."""
    return await create_user(
        db,
        email=body.email,
        full_name=body.full_name,
        password=body.password,
        roles=body.roles,
        org_id=current_user.organisation_id,
        creating_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/integration/test_user_management.py -k "create_user" -v
```

Expected: 7 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/user_service.py app/routers/admin.py tests/integration/test_user_management.py
git commit -m "feat(user): add POST /api/admin/users endpoint for user creation"
```

---

## Task 4: PATCH /api/admin/users/{id} (Update User)

**Files:**
- Modify: `app/services/user_service.py` (add `update_user`)
- Modify: `app/routers/admin.py` (append PATCH /users/{id})
- Modify: `tests/integration/test_user_management.py` (append PATCH section)

**Interfaces:**
- Consumes: `_get_user_in_org`, `_build_user_response`, `write_audit_log`
- Produces: `update_user(db, *, user_id, org_id, full_name, is_active, current_user_id, ip_address) -> UserResponse`

- [ ] **Step 1: Append failing PATCH tests to `tests/integration/test_user_management.py`**

```python
# ── PATCH /api/admin/users/{id} ───────────────────────────────────────────────

async def test_update_user_full_name_success(client, admin_user, org, db):
    """Admin can rename another user."""
    target = await _create_user_in_org(db, org=org, email="rename@test.com", password="Rename1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"full_name": "Renamed User"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Renamed User"
    assert resp.json()["id"] == target.id


async def test_update_user_deactivate_success(client, admin_user, org, db):
    """Admin can deactivate another user."""
    target = await _create_user_in_org(db, org=org, email="deactivate@test.com", password="Deact1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_update_user_deactivate_sets_tokens_invalidated_at(client, admin_user, org, db):
    """Deactivating a user sets tokens_invalidated_at in the same transaction."""
    target = await _create_user_in_org(db, org=org, email="revoke@test.com", password="Revoke1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    assert target.tokens_invalidated_at is None

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await db.refresh(target)
    assert target.tokens_invalidated_at is not None


async def test_update_user_admin_cannot_deactivate_self_returns_400(client, admin_user, org, db):
    """Admin cannot deactivate their own account."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{admin_user.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert "yourself" in resp.json()["detail"].lower() or "own" in resp.json()["detail"].lower()


async def test_update_user_writes_audit_log(client, admin_user, org, db):
    """PATCH writes an audit_logs row with before/after data."""
    target = await _create_user_in_org(db, org=org, email="auditpatch@test.com", password="AuditPatch1!", full_name="Original Name")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"full_name": "Updated Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user.update",
            AuditLog.resource_id == target.id,
            AuditLog.user_id == admin_user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.before_data["full_name"] == "Original Name"
    assert audit.after_data["full_name"] == "Updated Name"
    assert "password_hash" not in (audit.before_data or {})
    assert "password_hash" not in (audit.after_data or {})


async def test_update_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from a different org returns 404."""
    other_org = Organisation(name="Isolation Org", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(
        email="outsider2@test.com", full_name="Out", password_hash=_pwd_ctx.hash("Out1!"), is_active=True,
    )
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.commit()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.patch(
        f"{USERS_URL}/{outsider.id}",
        json={"full_name": "Hacked"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


async def test_update_user_empty_body_is_noop(client, admin_user, org, db):
    """PATCH with empty body returns 200 with unchanged data."""
    target = await _create_user_in_org(db, org=org, email="noop@test.com", password="Noop1!", full_name="Noop User")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Noop User"


async def test_update_user_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(db, org=org, email="target403@test.com", password="Target1!")
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"full_name": "Changed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_update_user_no_auth_returns_401(client, db):
    resp = await client.patch(f"{USERS_URL}/1", json={"full_name": "X"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_user_management.py -k "update_user" -v
```

Expected: FAIL — `404 Not Found` or `405 Method Not Allowed`

- [ ] **Step 3: Add `update_user` to `app/services/user_service.py`**

```python
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
            user.tokens_invalidated_at = datetime.now(timezone.utc)

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
```

- [ ] **Step 4: Append PATCH /users/{user_id} endpoint to `app/routers/admin.py`**

```python
@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
)
async def update_user_endpoint(
    user_id: int,
    body: UpdateUserRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update a user's name and/or active status. Deactivation revokes all sessions."""
    return await update_user(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        full_name=body.full_name,
        is_active=body.is_active,
        current_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/integration/test_user_management.py -k "update_user" -v
```

Expected: 9 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/user_service.py app/routers/admin.py tests/integration/test_user_management.py
git commit -m "feat(user): add PATCH /api/admin/users/{id} with deactivation and session revocation"
```

---

## Task 5: POST /api/admin/users/{id}/roles (Assign Role)

**Files:**
- Modify: `app/services/user_service.py` (add `assign_role`)
- Modify: `app/routers/admin.py` (append POST /users/{id}/roles)
- Modify: `tests/integration/test_user_management.py` (append assign_role section)

**Interfaces:**
- Produces: `assign_role(db, *, user_id, org_id, role: UserRoleEnum, assigning_user_id, ip_address) -> UserResponse`

- [ ] **Step 1: Append failing assign_role tests**

```python
# ── POST /api/admin/users/{id}/roles ─────────────────────────────────────────

async def test_assign_role_success(client, admin_user, org, db):
    """Admin can assign a new role to a user in their org."""
    target = await _create_user_in_org(db, org=org, email="assignrole@test.com", password="Assign1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    role_names = [r["role"] for r in resp.json()["roles"]]
    assert "packer" in role_names


async def test_assign_role_duplicate_returns_409(client, admin_user, org, db):
    """Assigning a role the user already has returns 409."""
    target = await _create_user_in_org(
        db, org=org, email="dup_role@test.com", password="DupRole1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 409


async def test_assign_role_writes_audit_log(client, admin_user, org, db):
    """POST /roles writes audit_logs with action admin.user_role.assign."""
    target = await _create_user_in_org(db, org=org, email="audit_role@test.com", password="AuditRole1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "inward_operator"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user_role.assign",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "user_roles"
    assert audit.after_data["user_id"] == target.id
    assert audit.after_data["role"] == "inward_operator"


async def test_assign_role_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from another org returns 404."""
    other_org = Organisation(name="Role Isolation", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(email="roleout@test.com", full_name="Out", password_hash=_pwd_ctx.hash("Out1!"), is_active=True)
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.commit()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.post(
        f"{USERS_URL}/{outsider.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_assign_role_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(db, org=org, email="target_r@test.com", password="Target1!")
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_assign_role_no_auth_returns_401(client, db):
    resp = await client.post(f"{USERS_URL}/1/roles", json={"role": "packer"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_user_management.py -k "assign_role" -v
```

Expected: FAIL

- [ ] **Step 3: Add `assign_role` to `app/services/user_service.py`**

```python
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
```

- [ ] **Step 4: Append POST /users/{user_id}/roles endpoint to `app/routers/admin.py`**

```python
@router.post(
    "/users/{user_id}/roles",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
)
async def assign_role_endpoint(
    user_id: int,
    body: AssignRoleRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Assign a role to a user within the admin's current organisation."""
    return await assign_role(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        role=body.role,
        assigning_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/integration/test_user_management.py -k "assign_role" -v
```

Expected: 6 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/user_service.py app/routers/admin.py tests/integration/test_user_management.py
git commit -m "feat(user): add POST /api/admin/users/{id}/roles endpoint for role assignment"
```

---

## Task 6: DELETE /api/admin/users/{id}/roles/{role} (Revoke Role)

**Files:**
- Modify: `app/services/user_service.py` (add `revoke_role`)
- Modify: `app/routers/admin.py` (append DELETE /users/{id}/roles/{role})
- Modify: `tests/integration/test_user_management.py` (append revoke_role section)

**Interfaces:**
- Produces: `revoke_role(db, *, user_id, org_id, role: UserRoleEnum, revoking_user_id, ip_address) -> UserResponse`

- [ ] **Step 1: Append failing revoke_role tests**

```python
# ── DELETE /api/admin/users/{id}/roles/{role} ─────────────────────────────────

async def test_revoke_role_success(client, admin_user, org, db):
    """Admin can revoke a role from a user; role no longer appears in response."""
    target = await _create_user_in_org(
        db, org=org, email="revoke_role@test.com", password="RevokeRole1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    role_names = [r["role"] for r in resp.json()["roles"]]
    assert "packer" not in role_names


async def test_revoke_role_not_assigned_returns_404(client, admin_user, org, db):
    """Revoking a role the user does not have returns 404."""
    target = await _create_user_in_org(db, org=org, email="no_role@test.com", password="NoRole1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


async def test_revoke_role_admin_cannot_revoke_own_admin_returns_400(client, admin_user, org, db):
    """Admin cannot revoke their own admin role."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{admin_user.id}/roles/admin",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert "own" in resp.json()["detail"].lower() or "yourself" in resp.json()["detail"].lower()


async def test_revoke_role_writes_audit_log(client, admin_user, org, db):
    """DELETE /roles/{role} writes audit_logs with action admin.user_role.revoke."""
    target = await _create_user_in_org(
        db, org=org, email="audit_revoke@test.com", password="AuditRev1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user_role.revoke",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "user_roles"
    assert audit.before_data["user_id"] == target.id
    assert audit.before_data["role"] == "packer"
    assert audit.after_data is None


async def test_revoke_role_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from another org returns 404."""
    other_org = Organisation(name="Revoke Isolation", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(email="revokeout@test.com", full_name="Out", password_hash=_pwd_ctx.hash("Out1!"), is_active=True)
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    db.add(UserRole(user_id=outsider.id, organisation_id=other_org.id, role=UserRoleEnum.packer, assigned_by=outsider.id))
    await db.commit()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.delete(
        f"{USERS_URL}/{outsider.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_revoke_role_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(
        db, org=org, email="target_rev@test.com", password="Target1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, packer_user, org, "PackerPass1!")
    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_revoke_role_no_auth_returns_401(client):
    resp = await client.delete(f"{USERS_URL}/1/roles/packer")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_user_management.py -k "revoke_role" -v
```

Expected: FAIL

- [ ] **Step 3: Add `revoke_role` to `app/services/user_service.py`**

```python
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not assigned to this user")

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
```

- [ ] **Step 4: Append DELETE /users/{user_id}/roles/{role} endpoint to `app/routers/admin.py`**

```python
@router.delete(
    "/users/{user_id}/roles/{role}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_role_endpoint(
    user_id: int,
    role: UserRoleEnum,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Revoke a role from a user within the admin's current organisation. Hard-deletes the user_roles row."""
    return await revoke_role(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        role=role,
        revoking_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/integration/test_user_management.py -k "revoke_role" -v
```

Expected: 7 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/services/user_service.py app/routers/admin.py tests/integration/test_user_management.py
git commit -m "feat(user): add DELETE /api/admin/users/{id}/roles/{role} for role revocation"
```

---

## Task 7: POST /api/admin/users/{id}/password-reset

**Files:**
- Modify: `app/services/user_service.py` (add `admin_password_reset`)
- Modify: `app/routers/admin.py` (append POST /users/{id}/password-reset)
- Modify: `tests/integration/test_user_management.py` (append password_reset section)

**Interfaces:**
- Produces: `admin_password_reset(db, *, user_id, org_id, new_password, admin_user_id, ip_address) -> None`

- [ ] **Step 1: Append failing password_reset tests**

```python
# ── POST /api/admin/users/{id}/password-reset ─────────────────────────────────

async def test_password_reset_success(client, admin_user, org, db):
    """Admin can reset another user's password; returns success message."""
    target = await _create_user_in_org(db, org=org, email="pwreset@test.com", password="OldPass1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/password-reset",
        json={"new_password": "NewPass2!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert "reset" in resp.json()["message"].lower() or "password" in resp.json()["message"].lower()


async def test_password_reset_new_password_works_for_login(client, admin_user, org, db):
    """After reset, user can log in with the new password."""
    target = await _create_user_in_org(db, org=org, email="newpwlogin@test.com", password="OldPass1!")
    admin_token = await _login(client, admin_user, org, "AdminPass1!")

    reset_resp = await client.post(
        f"{USERS_URL}/{target.id}/password-reset",
        json={"new_password": "BrandNew2!"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reset_resp.status_code == 200

    login_resp = await client.post(
        LOGIN_URL,
        json={"email": "newpwlogin@test.com", "password": "BrandNew2!", "organisation_id": org.id},
    )
    assert login_resp.status_code == 200


async def test_password_reset_invalidates_old_tokens(client, admin_user, org, db):
    """After admin resets password, the user's old token is rejected."""
    target = await _create_user_in_org(
        db, org=org, email="invalidate@test.com", password="OldPass1!", roles=[UserRoleEnum.packer]
    )
    old_token = await _login(client, target, org, "OldPass1!")

    admin_token = await _login(client, admin_user, org, "AdminPass1!")
    reset_resp = await client.post(
        f"{USERS_URL}/{target.id}/password-reset",
        json={"new_password": "FreshPass2!"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reset_resp.status_code == 200

    resp_with_old_token = await client.get(USERS_URL, headers={"Authorization": f"Bearer {old_token}"})
    assert resp_with_old_token.status_code == 401


async def test_password_reset_writes_audit_log_without_hash(client, admin_user, org, db):
    """password-reset audit log must not contain password_hash or plain password."""
    target = await _create_user_in_org(db, org=org, email="audit_pw@test.com", password="AuditPw1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/password-reset",
        json={"new_password": "AuditNew2!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user.password_reset",
            AuditLog.resource_id == target.id,
            AuditLog.user_id == admin_user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "users"
    assert "password_hash" not in (audit.after_data or {})
    assert "password" not in (audit.after_data or {})
    assert "new_password" not in (audit.after_data or {})


async def test_password_reset_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from another org returns 404."""
    other_org = Organisation(name="PwReset Isolation", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(email="pwout@test.com", full_name="Out", password_hash=_pwd_ctx.hash("Out1!"), is_active=True)
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.commit()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.post(
        f"{USERS_URL}/{outsider.id}/password-reset",
        json={"new_password": "Hacked1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_password_reset_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(db, org=org, email="target_pw@test.com", password="Target1!")
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/password-reset",
        json={"new_password": "NotAllowed1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_password_reset_no_auth_returns_401(client):
    resp = await client.post(f"{USERS_URL}/1/password-reset", json={"new_password": "NoAuth1!"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_user_management.py -k "password_reset" -v
```

Expected: FAIL

- [ ] **Step 3: Add `admin_password_reset` to `app/services/user_service.py`**

```python
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

    user.password_hash = _hash_password(new_password)
    user.tokens_invalidated_at = datetime.now(timezone.utc)

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
```

- [ ] **Step 4: Append POST /users/{user_id}/password-reset endpoint to `app/routers/admin.py`**

```python
@router.post(
    "/users/{user_id}/password-reset",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def admin_password_reset_endpoint(
    user_id: int,
    body: AdminPasswordResetRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Admin resets a user's password and revokes all their active sessions."""
    await admin_password_reset(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        new_password=body.new_password,
        admin_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return MessageResponse(message="Password reset successfully")
```

- [ ] **Step 5: Run the full test suite — all tests must pass**

```bash
pytest tests/integration/test_user_management.py -v
```

Expected: 37 PASSED (all tests across all tasks)

- [ ] **Step 6: Run the full project test suite to check for regressions**

```bash
pytest tests/ -v
```

Expected: All tests pass (including organisation management and auth tests)

- [ ] **Step 7: Commit**

```bash
git add app/services/user_service.py app/routers/admin.py tests/integration/test_user_management.py
git commit -m "feat(user): add POST /api/admin/users/{id}/password-reset with session invalidation"
```

---

## Self-Review

### Spec Coverage Gaps

| PRD requirement | Covered? | Notes |
|-----------------|----------|-------|
| GET /api/admin/users | ✅ Task 2 | Scoped to org, returns roles |
| POST /api/admin/users | ✅ Task 3 | Email uniqueness system-wide |
| PATCH /api/admin/users/{id} | ✅ Task 4 | name + active status |
| POST /api/admin/users/{id}/roles | ✅ Task 5 | |
| DELETE /api/admin/users/{id}/roles/{role} | ✅ Task 6 | Hard-delete, audit log |
| POST /api/admin/users/{id}/password-reset | ✅ Task 7 | |
| Admin cannot deactivate themselves | ✅ Task 4 | 400 + message |
| Admin cannot revoke own admin role | ✅ Task 6 | 400 + message |
| Deactivation revokes sessions in same TX | ✅ Task 4 | `tokens_invalidated_at` set in same commit |
| Email uniqueness system-wide | ✅ Task 3 | 409 on conflict |
| All queries scoped to org_id | ✅ All tasks | `_get_user_in_org` join enforces this |
| `password_hash` never in response/audit | ✅ All tasks | Service builds `UserResponse` manually; `after_data` constructed explicitly |
| Audit log before every commit | ✅ All tasks | `write_audit_log` called before `db.commit()` |
| RBAC: admin only | ✅ All tasks | `require_admin` on every endpoint |

### Risks and Assumptions

**R1 — Foundational files must be rebased in.** `app/models/user.py`, `app/models/enums.py`, `app/models/audit_log.py`, `app/database.py`, `app/dependencies/auth.py`, `app/services/auth_service.py`, `app/services/audit_service.py`, `app/schemas/common.py`, `app/utils/request.py`, `tests/conftest.py` are all expected from the `develop` branch (auth module). **Action required:** `git rebase develop` before Task 1 if not already done.

**R2 — `write_audit_log` signature assumed.** The plan uses the exact call pattern from `organisation_service.py`. If `audit_service.write_audit_log` has a different signature, adjust accordingly.

**R3 — `User` model has `tokens_invalidated_at` column.** Confirmed by the schema doc. If the ORM model does not have this mapped column, the PATCH and password-reset tasks will fail at runtime with a SQLAlchemy `AttributeError`. Check `app/models/user.py` before Task 4.

**R4 — `UserRole.id` is mapped.** The plan uses `role_row.id` as the `resource_id` in the revoke audit log. Confirmed by schema doc (user_roles has `id SERIAL PK`). Verify the ORM model maps this field.

**R5 — `conftest.py` fixtures.** The tests assume `admin_user` (password "AdminPass1!"), `packer_user` (password "PackerPass1!"), `org`, `client`, and `db` fixtures exist in `tests/conftest.py`. The test for `test_password_reset_invalidates_old_tokens` uses `_login(client, target, ...)` — this requires `target` to have a valid `.email` attribute, which `_create_user_in_org` sets.

**R6 — PRD ambiguity: "password-reset" means admin sets new password.** The endpoint body contains `new_password`. If the intent is "send reset email" instead (which would require email infra — not in V1 stack), the endpoint body and return type differ. The plan treats it as a direct password set, consistent with the V1 no-email-service constraint and the `tokens_invalidated_at` force-logout mechanism described in the PRD.

**R7 — Pagination not in PRD.** `GET /api/admin/users` returns all users in the org without pagination. For V1 with small org sizes, this is acceptable per YAGNI. If pagination is later required, the service signature will need `offset`/`limit` parameters.

**R8 — `db.get(User, user_id)` after commit.** In SQLAlchemy 2.0 async, `db.get()` issues a SELECT if the object is expired (which it is after commit). This is correct behaviour. If using a session with `expire_on_commit=False`, it would return the cached state — either way is safe here.
