"""Idempotent local-dev seed: creates the "Hexalog Dev" organisation and three
dev users (admin, packer, inward_operator) so a fresh checkout has a working login.

Run from the repo root with the project venv active:

    python -m scripts.seed_dev_users

Safe to run multiple times: existing users/roles/organisation are reused as-is
and passwords are never overwritten.
"""

import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.enums import AuditModuleEnum, UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole
from app.services.audit_service import write_audit_log
from app.services.password_service import hash_password

ORGANISATION_NAME = "Hexalog Dev"

DEV_USERS = [
    {
        "email": "admin@hexalog.in",
        "full_name": "Dev Admin",
        "password": "Admin@123",
        "role": UserRoleEnum.admin,
    },
    {
        "email": "packer@hexalog.in",
        "full_name": "Dev Packer",
        "password": "Packer@123",
        "role": UserRoleEnum.packer,
    },
    {
        "email": "inward@hexalog.in",
        "full_name": "Dev Inward Operator",
        "password": "Inward@123",
        "role": UserRoleEnum.inward_operator,
    },
]


async def _get_or_create_organisation(db) -> tuple[Organisation, bool]:
    result = await db.execute(select(Organisation).where(Organisation.name == ORGANISATION_NAME))
    org = result.scalar_one_or_none()
    if org is not None:
        return org, False

    org = Organisation(name=ORGANISATION_NAME, is_active=True)
    db.add(org)
    await db.flush()

    await write_audit_log(
        db,
        module=AuditModuleEnum.shared,
        action="dev_seed.organisation_created",
        resource_type="organisation",
        resource_id=org.id,
        organisation_id=org.id,
        after_data={"name": org.name},
    )
    return org, True


async def _get_or_create_user(db, org: Organisation, spec: dict) -> tuple[User, bool]:
    result = await db.execute(select(User).where(User.email == spec["email"]))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=spec["email"],
            password_hash=hash_password(spec["password"]),
            full_name=spec["full_name"],
            is_active=True,
        )
        db.add(user)
        await db.flush()
        created = True
    else:
        created = False

    link_result = await db.execute(
        select(UserOrganisation).where(
            UserOrganisation.user_id == user.id,
            UserOrganisation.organisation_id == org.id,
        )
    )
    if link_result.scalar_one_or_none() is None:
        db.add(UserOrganisation(user_id=user.id, organisation_id=org.id))

    role_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.organisation_id == org.id,
            UserRole.role == spec["role"],
        )
    )
    if role_result.scalar_one_or_none() is None:
        db.add(UserRole(user_id=user.id, organisation_id=org.id, role=spec["role"]))

    await db.flush()

    if created:
        await write_audit_log(
            db,
            module=AuditModuleEnum.shared,
            action="dev_seed.user_created",
            resource_type="user",
            resource_id=user.id,
            organisation_id=org.id,
            after_data={
                "email": user.email,
                "full_name": user.full_name,
                "role": spec["role"].value,
            },
        )

    return user, created


async def main() -> None:
    async with AsyncSessionLocal() as db:
        org, org_created = await _get_or_create_organisation(db)

        created_users = {}
        for spec in DEV_USERS:
            user, created = await _get_or_create_user(db, org, spec)
            created_users[spec["role"].value] = (user, created)

        await db.commit()

    org_status = "created" if org_created else "already existed"
    print(f"Organisation: {ORGANISATION_NAME} (id={org.id}) {org_status}")
    for role, (user, created) in created_users.items():
        status = "created" if created else "already existed"
        print(f"{role}: {user.email} ({status})")

    print()
    print(f"Organisation ID: {org.id}")
    print(f"Admin email: {DEV_USERS[0]['email']}")
    print(f"Packer email: {DEV_USERS[1]['email']}")
    print(f"Inward Operator email: {DEV_USERS[2]['email']}")
    print()
    print("Dev users are ready. Log in with each email, its password, and the org ID above.")
    print()
    print("To run this script again: python -m scripts.seed_dev_users")


if __name__ == "__main__":
    asyncio.run(main())
