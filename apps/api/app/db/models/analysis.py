"""Analysis jobs, AI analyses and the structured facts they produce."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.models.enums import AnalysisDepth, BillingPeriod, DataSource, JobStatus, JobType

# Read once at import: the vector column width is part of the schema, so changing it
# is a migration rather than a runtime switch.
EMBEDDING_DIM = get_settings().embedding_dimensions


class AnalysisJob(UUIDMixin, TimestampMixin, Base):
    """Durable state for a background job.

    Celery's own result backend is deliberately not the source of truth: it expires, it
    is not queryable per organization, and a user needs to see a failed job days later.
    """

    __tablename__ = "analysis_jobs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE")
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, native_enum=False, length=32), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.PENDING, nullable=False
    )
    depth: Mapped[AnalysisDepth] = mapped_column(
        Enum(AnalysisDepth, native_enum=False, length=32),
        default=AnalysisDepth.STANDARD,
        nullable=False,
    )

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    celery_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Hash of the inputs. Two jobs with the same fingerprint produce the same answer, so
    # the second one reuses the first result instead of paying for the AI call again.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_analysis_jobs_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_analysis_jobs_competitor_id_status", "competitor_id", "status"),
    )


class Analysis(UUIDMixin, TimestampMixin, Base):
    """One completed AI analysis of one competitor at one point in time."""

    __tablename__ = "analyses"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="SET NULL")
    )

    # Provenance of the analysis itself.  is_mock is surfaced through the API and shown
    # in the UI: development output must never be mistaken for real intelligence.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prompt_versions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    depth: Mapped[AnalysisDepth] = mapped_column(
        Enum(AnalysisDepth, native_enum=False, length=32),
        default=AnalysisDepth.STANDARD,
        nullable=False,
    )

    summary: Mapped[str | None] = mapped_column(Text)
    positioning: Mapped[str | None] = mapped_column(Text)
    target_audience: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    value_propositions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    strengths: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    weaknesses: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    marketing_channels: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    recommendations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    key_features: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    confidence: Mapped[float | None] = mapped_column(Float)
    data_completeness: Mapped[float | None] = mapped_column(Float)
    pages_analyzed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)

    # Set when the sanitizer detected instruction-like text in scraped content.  Surfaced
    # in the UI so a user can tell why an analysis looks odd.
    injection_flags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Corrections the pipeline applied to the model's output, e.g. a price that was
    # discarded because it did not appear on any crawled page.  Shown to the user.
    data_notes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_analyses_competitor_id_created_at", "competitor_id", "created_at"),
        Index("ix_analyses_organization_id_created_at", "organization_id", "created_at"),
    )


class Product(UUIDMixin, TimestampMixin, Base):
    """A product or service extracted from a competitor's site.

    Rows are versioned by ``is_current`` plus first/last seen, so the history of a
    competitor's catalogue survives without a separate history table.
    """

    __tablename__ = "products"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analyses.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Lower-cased, punctuation-stripped name. The identity key across analyses, so that
    # "Pro Plan" and "Pro plan." are recognised as the same product.
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(80))
    features: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, native_enum=False, length=32),
        default=DataSource.AI_INFERENCE,
        nullable=False,
    )

    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_products_competitor_id_is_current", "competitor_id", "is_current"),
        UniqueConstraint(
            "competitor_id", "normalized_name", name="uq_products_competitor_id_normalized_name"
        ),
    )


class PricingPlan(UUIDMixin, TimestampMixin, Base):
    """A pricing tier.

    ``amount`` is ``NUMERIC`` and is only populated when a price was actually parsed from
    page text.  When the site says "Contact us", ``amount`` stays NULL and
    ``is_custom_pricing`` is true — an AI-guessed number is rejected by validation.
    """

    __tablename__ = "pricing_plans"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analyses.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    billing_period: Mapped[BillingPeriod] = mapped_column(
        Enum(BillingPeriod, native_enum=False, length=32),
        default=BillingPeriod.UNKNOWN,
        nullable=False,
    )
    is_custom_pricing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    features: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    highlights: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, native_enum=False, length=32),
        default=DataSource.OBSERVED,
        nullable=False,
    )

    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_pricing_plans_competitor_id_is_current", "competitor_id", "is_current"),
        UniqueConstraint(
            "competitor_id",
            "normalized_name",
            "billing_period",
            name="uq_pricing_plans_competitor_id_normalized_name_billing_period",
        ),
    )


class Score(UUIDMixin, TimestampMixin, Base):
    """A competitive score with the inputs that produced it.

    ``dimensions`` holds, per dimension: the score (or null for insufficient data), the
    weight, the named inputs and a plain-language rationale.  The UI can therefore answer
    "why 82?" without re-running the scoring engine.
    """

    __tablename__ = "scores"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE")
    )

    overall: Mapped[float | None] = mapped_column(Float)
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    threat_level: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    data_completeness: Mapped[float | None] = mapped_column(Float)
    methodology_version: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (Index("ix_scores_competitor_id_created_at", "competitor_id", "created_at"),)


class Embedding(UUIDMixin, Base):
    """A vector chunk.

    The column type is created dynamically from ``EMBEDDING_DIMENSIONS`` at migration
    time; changing the dimension is a migration, not a runtime switch.
    """

    __tablename__ = "embeddings"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    __table_args__ = (
        Index("ix_embeddings_competitor_id_source_type", "competitor_id", "source_type"),
    )


__all__ = ["Analysis", "AnalysisJob", "Embedding", "PricingPlan", "Product", "Score"]
