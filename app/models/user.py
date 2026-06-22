import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import UserRoleEnum


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Brute-force protection (added by June 18 auth redesign)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user_organisations: Mapped[list["UserOrganisation"]] = relationship(
        back_populates="user", lazy="noload"
    )
    roles: Mapped[list["UserRole"]] = relationship(back_populates="user", lazy="noload")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", lazy="noload")


class UserOrganisation(Base):
    """Junction: which users belong to which organisations."""

    __tablename__ = "user_organisations"
    __table_args__ = (
        Index("idx_user_orgs_org_id", "organisation_id"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="user_organisations", foreign_keys=[user_id])
    organisation: Mapped["Organisation"] = relationship(  # noqa: F821
        back_populates="user_organisations"
    )


class UserRole(Base):
    """Per-org role assignment. Revoked by hard-delete (only hard-delete in the system)."""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "organisation_id", "role", name="uq_user_roles"),
        Index("idx_user_roles_org_id", "organisation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[UserRoleEnum] = mapped_column(
        Enum(UserRoleEnum, name="user_role_enum", native_enum=True), nullable=False
    )
    assigned_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="roles", foreign_keys=[user_id])


class Session(Base):
    """Active auth sessions. Refresh token is stored only as HMAC-SHA256 hash — never raw."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name="uq_sessions_refresh_token_hash"),
        Index(
            "idx_sessions_previous_refresh_token_hash",
            "previous_refresh_token_hash",
            postgresql_where=text("previous_refresh_token_hash IS NOT NULL"),
        ),
        Index("idx_sessions_user_id", "user_id"),
        Index(
            "idx_sessions_user_active",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    refresh_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    previous_refresh_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_token_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="sessions", foreign_keys=[user_id])


class LoginAttempt(Base):
    """Per-IP sliding-window login rate limit (no Redis required)."""

    __tablename__ = "login_attempts"

    ip_address: Mapped[str] = mapped_column(INET, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

