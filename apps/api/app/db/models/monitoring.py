"""Change detection, alerting, comparisons and reports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.models.enums import (
    ChangeType,
    JobStatus,
    NotificationChannel,
    NotificationStatus,
    ReportType,
    Severity,
)


class Change(UUIDMixin, Base):
    """A meaningful difference between two observations of a competitor.

    Only diffs that survive normalisation reach this table — a rotating nonce in the HTML
    is not a change, a plan going from EUR 49 to EUR 59 is.
    """

    __tablename__ = "changes"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analyses.id", ondelete="SET NULL")
    )

    change_type: Mapped[ChangeType] = mapped_column(
        Enum(ChangeType, native_enum=False, length=32), nullable=False
    )
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=16), default=Severity.LOW, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    entity_key: Mapped[str | None] = mapped_column(String(200))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    magnitude: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(String(2048))

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_changes_competitor_id_detected_at", "competitor_id", "detected_at"),
        Index("ix_changes_organization_id_detected_at", "organization_id", "detected_at"),
        Index("ix_changes_organization_id_severity", "organization_id", "severity"),
    )


class AlertRule(UUIDMixin, TimestampMixin, Base):
    """User configuration for which changes are worth interrupting them about."""

    __tablename__ = "alert_rules"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # NULL means "every competitor in this organization".
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE")
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    change_types: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, nullable=False)
    min_severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=16), default=Severity.MEDIUM, nullable=False
    )
    channels: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(String(2048))

    __table_args__ = (
        Index("ix_alert_rules_organization_id_is_active", "organization_id", "is_active"),
    )


class Notification(UUIDMixin, Base):
    """Delivery record for one change to one channel.

    Persisted before delivery is attempted, so a failed send is visible rather than lost.
    """

    __tablename__ = "notifications"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    alert_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="SET NULL")
    )
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("changes.id", ondelete="CASCADE")
    )

    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, native_enum=False, length=20), nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, native_enum=False, length=20),
        default=NotificationStatus.PENDING,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_notifications_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_notifications_organization_id_status", "organization_id", "status"),
    )


class Comparison(UUIDMixin, TimestampMixin, Base):
    """A saved multi-competitor comparison."""

    __tablename__ = "comparisons"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(String(160))
    competitor_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False
    )

    # Deterministic score matrix, computed from stored scores — no AI involved.
    matrix: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # AI narrative: strongest competitor, biggest threat, differentiation opportunities.
    insights: Mapped[dict | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(32))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_comparisons_organization_id_created_at", "organization_id", "created_at"),
    )


class Report(UUIDMixin, TimestampMixin, Base):
    """A generated report document.

    Stored as structured JSON (sections, tables, series) rather than rendered HTML, so
    the same document can be rendered in-app today and exported to PDF later without
    regenerating it.
    """

    __tablename__ = "reports"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    report_type: Mapped[ReportType] = mapped_column(
        Enum(ReportType, native_enum=False, length=40), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.PENDING, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    content: Mapped[dict | None] = mapped_column(JSONB)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_reports_organization_id_created_at", "organization_id", "created_at"),
    )


__all__ = ["AlertRule", "Change", "Comparison", "Notification", "Report"]
