"""Dashboard aggregation.

The overview page is the first thing a user sees after login, so it gets one function
issuing a fixed number of queries rather than a router assembling a dozen.  Nothing here
loops over competitors issuing per-row queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantScope
from app.db.base import utcnow
from app.db.models.analysis import Analysis, AnalysisJob, Score
from app.db.models.competitor import Competitor
from app.db.models.enums import CompetitorStatus, JobStatus, Severity
from app.db.models.monitoring import Change

THREAT_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "unknown": 4}


@dataclass(slots=True)
class DashboardOverview:
    competitors_tracked: int
    competitors_analyzed: int
    competitors_archived: int
    average_score: float | None
    threat_distribution: dict[str, int]
    overall_threat_level: str
    changes_last_7_days: int
    high_severity_changes: int
    running_jobs: int
    failed_jobs: int
    uses_mock_ai: bool
    recent_changes: list[dict[str, Any]] = field(default_factory=list)
    recent_analyses: list[dict[str, Any]] = field(default_factory=list)
    landscape: list[dict[str, Any]] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)


async def overview(session: AsyncSession, scope: TenantScope) -> DashboardOverview:
    org_id = scope.organization_id
    week_ago = utcnow() - timedelta(days=7)

    counts_result = await session.execute(
        select(
            func.count(Competitor.id).filter(Competitor.status == CompetitorStatus.ACTIVE),
            func.count(Competitor.id).filter(Competitor.status == CompetitorStatus.ARCHIVED),
            func.count(Competitor.id).filter(Competitor.last_analyzed_at.is_not(None)),
            func.avg(Competitor.latest_overall_score),
        ).where(Competitor.organization_id == org_id, Competitor.deleted_at.is_(None))
    )
    active, archived, analyzed, average = counts_result.one()

    # Latest score per competitor, in one query.
    latest_scores_result = await session.execute(
        select(Score)
        .join(Competitor, Competitor.id == Score.competitor_id)
        .where(
            Score.organization_id == org_id,
            Competitor.deleted_at.is_(None),
            Competitor.status == CompetitorStatus.ACTIVE,
        )
        .distinct(Score.competitor_id)
        .order_by(Score.competitor_id, Score.created_at.desc())
    )
    latest_scores = list(latest_scores_result.scalars().all())

    distribution: dict[str, int] = {}
    for score in latest_scores:
        distribution[score.threat_level] = distribution.get(score.threat_level, 0) + 1

    change_counts_result = await session.execute(
        select(
            func.count(Change.id),
            func.count(Change.id).filter(Change.severity == Severity.HIGH),
        ).where(Change.organization_id == org_id, Change.detected_at >= week_ago)
    )
    changes_total, changes_high = change_counts_result.one()

    job_counts_result = await session.execute(
        select(
            func.count(AnalysisJob.id).filter(
                AnalysisJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
            ),
            func.count(AnalysisJob.id).filter(AnalysisJob.status == JobStatus.FAILED),
        ).where(AnalysisJob.organization_id == org_id, AnalysisJob.created_at >= week_ago)
    )
    running, failed = job_counts_result.one()

    recent_changes_result = await session.execute(
        select(Change, Competitor.name, Competitor.favicon_url)
        .join(Competitor, Competitor.id == Change.competitor_id)
        .where(Change.organization_id == org_id)
        .order_by(Change.detected_at.desc())
        .limit(8)
    )
    recent_changes = [
        {
            "id": str(change.id),
            "competitor_id": str(change.competitor_id),
            "competitor_name": name,
            "favicon_url": favicon,
            "change_type": change.change_type.value,
            "severity": change.severity.value,
            "title": change.title,
            "detected_at": change.detected_at,
            "acknowledged": change.acknowledged_at is not None,
        }
        for change, name, favicon in recent_changes_result.all()
    ]

    recent_analyses_result = await session.execute(
        select(Analysis, Competitor.name)
        .join(Competitor, Competitor.id == Analysis.competitor_id)
        .where(Analysis.organization_id == org_id)
        .order_by(Analysis.created_at.desc())
        .limit(6)
    )
    recent_analyses = [
        {
            "id": str(analysis.id),
            "competitor_id": str(analysis.competitor_id),
            "competitor_name": name,
            "created_at": analysis.created_at,
            "confidence": analysis.confidence,
            "is_mock": analysis.is_mock,
            "pages_analyzed": analysis.pages_analyzed,
        }
        for analysis, name in recent_analyses_result.all()
    ]

    landscape_result = await session.execute(
        select(Competitor)
        .where(
            Competitor.organization_id == org_id,
            Competitor.deleted_at.is_(None),
            Competitor.status == CompetitorStatus.ACTIVE,
        )
        .order_by(Competitor.latest_overall_score.desc().nullslast())
        .limit(12)
    )
    scores_by_competitor = {score.competitor_id: score for score in latest_scores}
    landscape = []
    for competitor in landscape_result.scalars().all():
        score = scores_by_competitor.get(competitor.id)
        landscape.append(
            {
                "id": str(competitor.id),
                "name": competitor.name,
                "domain": competitor.domain,
                "favicon_url": competitor.favicon_url,
                "overall": competitor.latest_overall_score,
                "threat_level": score.threat_level if score else "unknown",
                "importance": competitor.importance.value,
                "last_analyzed_at": competitor.last_analyzed_at,
                "dimensions": (score.dimensions if score else {}),
            }
        )

    uses_mock = any(analysis["is_mock"] for analysis in recent_analyses)

    return DashboardOverview(
        competitors_tracked=int(active or 0),
        competitors_analyzed=int(analyzed or 0),
        competitors_archived=int(archived or 0),
        average_score=round(float(average), 1) if average is not None else None,
        threat_distribution=distribution,
        overall_threat_level=_aggregate_threat(distribution),
        changes_last_7_days=int(changes_total or 0),
        high_severity_changes=int(changes_high or 0),
        running_jobs=int(running or 0),
        failed_jobs=int(failed or 0),
        uses_mock_ai=uses_mock,
        recent_changes=recent_changes,
        recent_analyses=recent_analyses,
        landscape=landscape,
        opportunities=_derive_opportunities(latest_scores, landscape),
    )


def _aggregate_threat(distribution: dict[str, int]) -> str:
    """The market's threat level is its worst competitor, not its average.

    Averaging would let five harmless competitors hide one dangerous one.
    """
    for level in ("critical", "high", "moderate", "low"):
        if distribution.get(level):
            return level
    return "unknown"


def _derive_opportunities(
    scores: list[Score], landscape: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Gaps visible in the score matrix.

    Deterministic, and derived only from dimensions that actually have data: a dimension
    everyone scores badly on is a market-wide gap; one nobody has data for is not.
    """
    if not scores:
        return []

    by_dimension: dict[str, list[float]] = {}
    for score in scores:
        for dimension, entry in (score.dimensions or {}).items():
            value = entry.get("score") if isinstance(entry, dict) else None
            if value is not None:
                by_dimension.setdefault(dimension, []).append(float(value))

    opportunities: list[dict[str, Any]] = []
    for dimension, values in by_dimension.items():
        # Require a majority of competitors to have data before calling it a market gap.
        if len(values) < max(2, len(scores) // 2):
            continue
        average = sum(values) / len(values)
        if average < 55:
            opportunities.append(
                {
                    "dimension": dimension,
                    "market_average": round(average),
                    "competitors_measured": len(values),
                    "title": f"Weak {dimension.replace('_', ' ')} across tracked competitors",
                    "detail": (
                        f"The competitors we could measure average {round(average)}/100 on "
                        f"{dimension.replace('_', ' ')}. That is an area where differentiation "
                        "is comparatively cheap."
                    ),
                }
            )

    opportunities.sort(key=lambda item: item["market_average"])
    _ = landscape
    return opportunities[:4]


__all__ = ["THREAT_ORDER", "DashboardOverview", "overview"]
