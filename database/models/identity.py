from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from database.enums import MemberRole


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(sa.String(200))
    slug: Mapped[str] = mapped_column(sa.String(100), unique=True)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        sa.Index("uq_users_email_lower", sa.func.lower(sa.text("email")), unique=True),
    )

    email: Mapped[str] = mapped_column(sa.String(320))
    full_name: Mapped[str | None] = mapped_column(sa.String(200))
    # NULL until the user sets a password (invite pending / SSO).
    password_hash: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(server_default=sa.true())
    is_superuser: Mapped[bool] = mapped_column(server_default=sa.false())
    last_login_at: Mapped[datetime | None]


class OrganizationMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "organization_members"
    __table_args__ = (sa.UniqueConstraint("organization_id", "user_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MemberRole] = mapped_column(str_enum(MemberRole, "member_role"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ApiKey(UUIDPrimaryKeyMixin, Base):
    """Only a hash of the key is stored; the plaintext is shown once at creation."""

    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(sa.String(200))
    key_prefix: Mapped[str] = mapped_column(sa.String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(sa.String(128))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), server_default="{}")
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
