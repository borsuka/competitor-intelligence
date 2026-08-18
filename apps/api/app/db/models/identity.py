"""Identity and tenancy tables: users, sessions, organizations, membership, audit."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenancy import Role
from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.db.models.enums import OrgPlan, VerificationPurpose


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Stored lower-cased by the auth service so uniqueness is case-insensitive without
    # depending on the citext extension.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None


class RefreshToken(UUIDMixin, TimestampMixin, Base):
    """One row per issued refresh token.

    Tokens rotate: using one revokes it and issues a successor.  ``family_id`` links a
    rotation chain so that replaying an already-used token can revoke the whole family —
    the standard detection for a stolen token.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),)


class VerificationToken(UUIDMixin, TimestampMixin, Base):
    """Single-use, hashed tokens for email verification and password reset."""

    __tablename__ = "verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[VerificationPurpose] = mapped_column(
        Enum(VerificationPurpose, native_enum=False, length=32), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_verification_tokens_user_id_purpose", "user_id", "purpose"),)


class Organization(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    plan: Mapped[OrgPlan] = mapped_column(
        Enum(OrgPlan, native_enum=False, length=32), default=OrgPlan.FREE, nullable=False
    )
    # The user's own company, used to make recommendations concrete rather than generic.
    own_company_name: Mapped[str | None] = mapped_column(String(120))
    own_company_url: Mapped[str | None] = mapped_column(String(2048))
    own_company_description: Mapped[str | None] = mapped_column(Text)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Membership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "memberships"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32), default=Role.MEMBER, nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_memberships_organization_id_user_id"),
        Index("ix_memberships_user_id", "user_id"),
    )


class Invitation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32), default=Role.MEMBER, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_invitations_organization_id_email", "organization_id", "email"),
    )


class UsageCounter(UUIDMixin, TimestampMixin, Base):
    """Per-organization usage for the current billing month.

    Kept as counters rather than derived from a scan of the analyses table: quota checks
    run on every enqueue and must be a single indexed row read.
    """

    __tablename__ = "usage_counters"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    analyses_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ai_tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ai_tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "period", name="uq_usage_counters_organization_id_period"),
    )


class AuditLog(UUIDMixin, Base):
    """Append-only record of security-relevant actions.

    No ``updated_at`` and no ORM writes outside :mod:`app.services.audit` — an audit row
    that can be edited is not an audit row.
    """

    __tablename__ = "audit_logs"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_audit_logs_organization_id_created_at", "organization_id", "created_at"),
    )


__all__ = [
    "User",
    "RefreshToken",
    "VerificationToken",
    "Organization",
    "Membership",
    "Invitation",
    "UsageCounter",
    "AuditLog",
]
