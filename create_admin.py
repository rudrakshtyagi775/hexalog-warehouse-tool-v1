import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — registers all ORM models
from app.config import settings
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole
from app.services.password_service import hash_password

engine = create_async_engine(settings.DATABASE_URL)
Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

EMAIL = "admin@hexalog.in"
PASSWORD = "Admin@1234"

async def main():
    async with Session() as db:
        existing = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
        if existing:
            print(f"User {EMAIL} already exists (id={existing.id}) — skipping.")
            return

        org = Organisation(name="Hexalog", is_active=True)
        db.add(org)
        await db.flush()

        user = User(
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            full_name="Admin",
            is_active=True,
        )
        db.add(user)
        await db.flush()

        db.add(UserOrganisation(user_id=user.id, organisation_id=org.id))
        db.add(UserRole(user_id=user.id, organisation_id=org.id, role=UserRoleEnum.admin))

        await db.commit()
        print(f"Created: org_id={org.id} user_id={user.id} email={EMAIL}")

asyncio.run(main())
