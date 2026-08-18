"""Competitors and the raw material collected about them."""

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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.db.models.enums import CompetitorStatus, Importance, PageType


class Competitor(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "competitors"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    website_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Registrable host, lower-cased, no "www.".  The dedupe key within an organization.
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    favicon_url: Mapped[str | None] = mapped_column(String(2048))

    category: Mapped[str | None] = mapped_column(String(80))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[CompetitorStatus] = mapped_column(
        Enum(CompetitorStatus, native_enum=False, length=32),
        default=CompetitorStatus.ACTIVE,
        nullable=False,
    )
    importance: Mapped[Importance] = mapped_column(
        Enum(Importance, native_enum=False, length=32), default=Importance.MEDIUM, nullable=False
    )

    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    monitoring_interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    next_monitor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # Denormalised for list views — recomputed whenever an analysis completes.  Keeping
    # them here avoids a correlated subquery per row on the competitors table.
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_overall_score: Mapped[float | None] = mapped_column(Float)
    latest_analysis_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    pages: Mapped[list["CompetitorPage"]] = relationship(
        back_populates="competitor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "domain", name="uq_competitors_organization_id_domain"),
        Index("ix_competitors_organization_id_status", "organization_id", "status"),
        Index("ix_competitors_organization_id_created_at", "organization_id", "created_at"),
    )


class CompetitorPage(UUIDMixin, TimestampMixin, Base):
    """A URL discovered on a competitor's site, classified by purpose."""

    __tablename__ = "competitor_pages"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # SHA-256 of the normalised URL.  A unique index on a 2048-char column is not
    # possible in PostgreSQL (btree row-size limit), so uniqueness is on the hash.
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_type: Mapped[PageType] = mapped_column(
        Enum(PageType, native_enum=False, length=32), default=PageType.OTHER, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(512))
    discovery_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    competitor: Mapped[Competitor] = relationship(back_populates="pages")
    snapshots: Mapped[list["PageSnapshot"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("competitor_id", "url_hash", name="uq_competitor_pages_competitor_id_url_hash"),
        Index("ix_competitor_pages_competitor_id_page_type", "competitor_id", "page_type"),
    )


class PageSnapshot(UUIDMixin, Base):
    """One fetch of one page.

    ``content_hash`` covers the raw body; ``text_hash`` covers normalised, boilerplate
    stripped text.  Change detection compares ``text_hash`` so that a rotating CSRF token
    or a build id in the HTML does not register as a change.
    """

    __tablename__ = "page_snapshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitor_pages.id", ondelete="CASCADE"), nullable=False
    )

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    title: Mapped[str | None] = mapped_column(String(512))
    meta_description: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(String(2048))
    lang: Mapped[str | None] = mapped_column(String(16))

    headings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    structured_data: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    open_graph: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    links: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    detected_prices: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    calls_to_action: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    render_mode: Mapped[str] = mapped_column(String(16), default="http", nullable=False)
    fetch_duration_ms: Mapped[int | None] = mapped_column(Integer)

    page: Mapped[CompetitorPage] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("ix_page_snapshots_page_id_fetched_at", "page_id", "fetched_at"),
        Index("ix_page_snapshots_competitor_id_fetched_at", "competitor_id", "fetched_at"),
    )


class SeoSnapshot(UUIDMixin, Base):
    """Observed on-page SEO signals for one crawl.

    Every field here is measured from fetched HTML.  Nothing that would require a
    third-party rank/backlink tool is stored, because we cannot observe it.
    """

    __tablename__ = "seo_snapshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pages_crawled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_discovered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_sitemap: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sitemap_url_count: Mapped[int | None] = mapped_column(Integer)
    has_robots_txt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_blog: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    pages_missing_title: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_missing_meta_description: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_missing_h1: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_title_length: Mapped[float | None] = mapped_column(Float)
    avg_word_count: Mapped[float | None] = mapped_column(Float)
    internal_link_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    external_link_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    structured_data_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    content_topics: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    __table_args__ = (
        Index("ix_seo_snapshots_competitor_id_captured_at", "competitor_id", "captured_at"),
    )


__all__ = ["Competitor", "CompetitorPage", "PageSnapshot", "SeoSnapshot"]
