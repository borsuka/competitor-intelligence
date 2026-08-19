"""Competitor CRUD, always scoped to one organization.

Every query in this module filters on ``scope.organization_id``.  There is no "get by id"
that skips the tenant filter — a missing competitor and someone else's competitor are the
same 404, which is what stops the API from confirming that another organization's ids
exist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.tenancy import Role, TenantScope
from app.db.base import utcnow
from app.db.models.analysis import Analysis, PricingPlan, Product, Score
from app.db.models.competitor import Competitor, CompetitorPage, PageSnapshot
from app.db.models.enums import CompetitorStatus, Importance
from app.db.models.monitoring import Change
from app.scraping.urls import assert_safe_url, extract_domain, normalize_url
from app.services import audit, organizations

# How often each importance level is re-checked by the scheduler.  A critical competitor
# is worth a daily crawl; a low-importance one is not worth the spend.
MONITORING_INTERVALS: dict[Importance, int] = {
    Importance.CRITICAL: 12,
    Importance.HIGH: 24,
    Importance.MEDIUM: 72,
    Importance.LOW: 168,
}

MAX_TAGS = 12
MAX_TAG_LENGTH = 40


@dataclass(slots=True)
class CompetitorFilters:
    search: str | None = None
    status: CompetitorStatus | None = CompetitorStatus.ACTIVE
    category: str | None = None
    tags: list[str] | None = None
    importance: Importance | None = None


@dataclass(slots=True)
class Page[T]:
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


def validate_website_url(url: str) -> tuple[str, str]:
    """Validate and normalise a competitor URL.

    Runs the full SSRF guard at creation time rather than only at crawl time, so a user
    gets an immediate, actionable error instead of a job that fails an hour later.
    """
    candidate = url.strip()
    if not candidate:
        raise ValidationError("A website URL is required.", code="url_required")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    assert_safe_url(candidate)  # raises UnsafeURLError (422) with a stable code

    normalized = normalize_url(candidate)
    domain = extract_domain(normalized)
    if not domain or "." not in domain:
        raise ValidationError("That does not look like a website address.", code="url_invalid")
    return normalized, domain


def clean_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = tag.strip()[:MAX_TAG_LENGTH]
        if value and value.lower() not in seen:
            seen.add(value.lower())
            cleaned.append(value)
    return cleaned[:MAX_TAGS]


def _scoped(scope: TenantScope) -> Select[tuple[Competitor]]:
    return select(Competitor).where(
        Competitor.organization_id == scope.organization_id,
        Competitor.deleted_at.is_(None),
    )


async def get(session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID) -> Competitor:
    result = await session.execute(_scoped(scope).where(Competitor.id == competitor_id))
    competitor = result.scalar_one_or_none()
    if competitor is None:
        raise NotFoundError("Competitor not found.", code="competitor_not_found")
    return competitor


async def list_competitors(
    session: AsyncSession,
    scope: TenantScope,
    *,
    filters: CompetitorFilters | None = None,
    limit: int = 25,
    offset: int = 0,
    sort: str = "created_at",
) -> Page[Competitor]:
    filters = filters or CompetitorFilters()
    query = _scoped(scope)

    if filters.status is not None:
        query = query.where(Competitor.status == filters.status)
    if filters.category:
        query = query.where(Competitor.category == filters.category)
    if filters.importance is not None:
        query = query.where(Competitor.importance == filters.importance)
    if filters.tags:
        query = query.where(Competitor.tags.overlap(filters.tags))
    if filters.search:
        pattern = f"%{filters.search.strip()}%"
        query = query.where(or_(Competitor.name.ilike(pattern), Competitor.domain.ilike(pattern)))

    sort_columns = {
        "created_at": Competitor.created_at.desc(),
        "name": Competitor.name.asc(),
        "score": Competitor.latest_overall_score.desc().nullslast(),
        "last_analyzed": Competitor.last_analyzed_at.desc().nullslast(),
    }
    query = query.order_by(sort_columns.get(sort, Competitor.created_at.desc()))

    # count() over the filtered query, not the table: pagination totals must respect
    # filters or the UI shows the wrong page count.
    total_result = await session.execute(
        select(func.count()).select_from(query.order_by(None).subquery())
    )
    total = int(total_result.scalar_one() or 0)

    result = await session.execute(query.limit(min(limit, 100)).offset(max(offset, 0)))
    return Page(items=list(result.scalars().all()), total=total, limit=limit, offset=offset)


async def create(
    session: AsyncSession,
    scope: TenantScope,
    *,
    name: str | None,
    website_url: str,
    category: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    importance: Importance = Importance.MEDIUM,
) -> Competitor:
    scope.require(Role.MEMBER)
    await organizations.assert_can_add_competitor(session, scope.organization_id)

    normalized_url, domain = validate_website_url(website_url)

    existing = await session.execute(
        select(Competitor).where(
            Competitor.organization_id == scope.organization_id, Competitor.domain == domain
        )
    )
    duplicate = existing.scalar_one_or_none()
    if duplicate is not None:
        if duplicate.deleted_at is None:
            raise ConflictError(
                f"{domain} is already tracked by this organization.",
                code="competitor_exists",
                details={"competitor_id": str(duplicate.id)},
            )
        # Re-adding a previously deleted competitor restores it rather than colliding
        # with the unique index.
        duplicate.deleted_at = None
        duplicate.status = CompetitorStatus.ACTIVE
        duplicate.website_url = normalized_url
        duplicate.name = (name or domain).strip()[:160]
        return duplicate

    interval = MONITORING_INTERVALS.get(importance, 24)
    competitor = Competitor(
        organization_id=scope.organization_id,
        created_by_user_id=scope.user_id,
        name=(name or domain).strip()[:160],
        website_url=normalized_url,
        domain=domain,
        category=(category or "").strip()[:80] or None,
        tags=clean_tags(tags),
        notes=notes,
        importance=importance,
        monitoring_interval_hours=interval,
        next_monitor_at=utcnow() + timedelta(hours=interval),
        favicon_url=f"https://{domain}/favicon.ico",
    )
    session.add(competitor)
    await session.flush()

    await audit.record(
        session,
        action="competitor.created",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="competitor",
        resource_id=competitor.id,
        metadata={"domain": domain},
    )
    return competitor


async def update(
    session: AsyncSession,
    scope: TenantScope,
    competitor_id: uuid.UUID,
    **fields: Any,
) -> Competitor:
    scope.require(Role.MEMBER)
    competitor = await get(session, scope, competitor_id)

    if (name := fields.get("name")) is not None:
        competitor.name = name.strip()[:160]
    if "category" in fields:
        category = fields["category"]
        competitor.category = (category or "").strip()[:80] or None
    if "notes" in fields:
        competitor.notes = fields["notes"]
    if "tags" in fields:
        competitor.tags = clean_tags(fields["tags"])
    if (importance := fields.get("importance")) is not None:
        competitor.importance = importance
        competitor.monitoring_interval_hours = MONITORING_INTERVALS.get(importance, 24)
    if (status := fields.get("status")) is not None:
        competitor.status = status
        # Archiving stops the spend: an archived competitor is not crawled.
        competitor.monitoring_enabled = status is CompetitorStatus.ACTIVE
    if (monitoring := fields.get("monitoring_enabled")) is not None:
        competitor.monitoring_enabled = monitoring
    if (interval := fields.get("monitoring_interval_hours")) is not None:
        competitor.monitoring_interval_hours = max(6, min(int(interval), 24 * 30))

    if competitor.monitoring_enabled:
        competitor.next_monitor_at = utcnow() + timedelta(
            hours=competitor.monitoring_interval_hours
        )
    else:
        competitor.next_monitor_at = None

    await audit.record(
        session,
        action="competitor.updated",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="competitor",
        resource_id=competitor.id,
        metadata={"fields": sorted(fields.keys())},
    )
    return competitor


async def archive(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> Competitor:
    return await update(session, scope, competitor_id, status=CompetitorStatus.ARCHIVED)


async def delete(session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID) -> None:
    """Soft delete.

    The competitor's analyses, snapshots and change history stay in place: a mis-click
    should be recoverable, and hard-deleting months of intelligence is not.
    """
    scope.require(Role.ADMIN)
    competitor = await get(session, scope, competitor_id)
    competitor.deleted_at = utcnow()
    competitor.monitoring_enabled = False
    competitor.next_monitor_at = None

    await audit.record(
        session,
        action="competitor.deleted",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="competitor",
        resource_id=competitor.id,
    )


# ------------------------------------------------------------ related records


async def latest_analysis(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> Analysis | None:
    result = await session.execute(
        select(Analysis)
        .where(
            Analysis.organization_id == scope.organization_id,
            Analysis.competitor_id == competitor_id,
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_analyses(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID, *, limit: int = 20
) -> list[Analysis]:
    result = await session.execute(
        select(Analysis)
        .where(
            Analysis.organization_id == scope.organization_id,
            Analysis.competitor_id == competitor_id,
        )
        .order_by(Analysis.created_at.desc())
        .limit(min(limit, 100))
    )
    return list(result.scalars().all())


async def current_products(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> list[Product]:
    result = await session.execute(
        select(Product)
        .where(
            Product.organization_id == scope.organization_id,
            Product.competitor_id == competitor_id,
            Product.is_current.is_(True),
        )
        .order_by(Product.name)
    )
    return list(result.scalars().all())


async def current_pricing(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> list[PricingPlan]:
    result = await session.execute(
        select(PricingPlan)
        .where(
            PricingPlan.organization_id == scope.organization_id,
            PricingPlan.competitor_id == competitor_id,
            PricingPlan.is_current.is_(True),
        )
        .order_by(PricingPlan.amount.asc().nullslast())
    )
    return list(result.scalars().all())


async def pricing_history(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> list[PricingPlan]:
    result = await session.execute(
        select(PricingPlan)
        .where(
            PricingPlan.organization_id == scope.organization_id,
            PricingPlan.competitor_id == competitor_id,
        )
        .order_by(PricingPlan.normalized_name, PricingPlan.first_seen_at)
    )
    return list(result.scalars().all())


async def latest_score(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> Score | None:
    result = await session.execute(
        select(Score)
        .where(Score.organization_id == scope.organization_id, Score.competitor_id == competitor_id)
        .order_by(Score.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def score_history(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID, *, limit: int = 30
) -> list[Score]:
    result = await session.execute(
        select(Score)
        .where(Score.organization_id == scope.organization_id, Score.competitor_id == competitor_id)
        .order_by(Score.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def list_changes(
    session: AsyncSession,
    scope: TenantScope,
    competitor_id: uuid.UUID | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    min_severity_rank: int = 0,
) -> Page[Change]:
    query = select(Change).where(Change.organization_id == scope.organization_id)
    if competitor_id is not None:
        query = query.where(Change.competitor_id == competitor_id)
    if min_severity_rank > 0:
        allowed = [s.value for s in _severities_at_or_above(min_severity_rank)]
        query = query.where(Change.severity.in_(allowed))

    total_result = await session.execute(select(func.count()).select_from(query.subquery()))
    result = await session.execute(
        query.order_by(Change.detected_at.desc()).limit(min(limit, 100)).offset(max(offset, 0))
    )
    return Page(
        items=list(result.scalars().all()),
        total=int(total_result.scalar_one() or 0),
        limit=limit,
        offset=offset,
    )


def _severities_at_or_above(rank: int):
    from app.db.models.enums import Severity

    return [severity for severity in Severity if severity.rank >= rank]


async def list_pages(
    session: AsyncSession, scope: TenantScope, competitor_id: uuid.UUID
) -> list[CompetitorPage]:
    result = await session.execute(
        select(CompetitorPage)
        .where(
            CompetitorPage.organization_id == scope.organization_id,
            CompetitorPage.competitor_id == competitor_id,
            CompetitorPage.is_active.is_(True),
        )
        .order_by(CompetitorPage.discovery_score.desc())
    )
    return list(result.scalars().all())


async def latest_snapshots(session: AsyncSession, competitor_id: uuid.UUID) -> list[PageSnapshot]:
    """Most recent snapshot per page.

    ``DISTINCT ON`` keeps this to one query; the obvious alternative — fetch pages, then
    fetch the newest snapshot for each — is a textbook N+1.
    """
    query = (
        select(PageSnapshot)
        .where(PageSnapshot.competitor_id == competitor_id)
        .distinct(PageSnapshot.page_id)
        .order_by(PageSnapshot.page_id, PageSnapshot.fetched_at.desc())
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def due_for_monitoring(
    session: AsyncSession, *, limit: int | None = None
) -> list[Competitor]:
    """Competitors whose monitoring interval has elapsed. Used by the scheduler."""
    settings = get_settings()
    result = await session.execute(
        select(Competitor)
        .where(
            Competitor.deleted_at.is_(None),
            Competitor.status == CompetitorStatus.ACTIVE,
            Competitor.monitoring_enabled.is_(True),
            Competitor.next_monitor_at.is_not(None),
            Competitor.next_monitor_at <= utcnow(),
        )
        .order_by(Competitor.next_monitor_at)
        .limit(limit or settings.monitoring_batch_size)
    )
    return list(result.scalars().all())


__all__ = [
    "MONITORING_INTERVALS",
    "CompetitorFilters",
    "Page",
    "archive",
    "clean_tags",
    "create",
    "current_pricing",
    "current_products",
    "delete",
    "due_for_monitoring",
    "get",
    "latest_analysis",
    "latest_score",
    "latest_snapshots",
    "list_analyses",
    "list_changes",
    "list_competitors",
    "list_pages",
    "pricing_history",
    "score_history",
    "update",
    "validate_website_url",
]
