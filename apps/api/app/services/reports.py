"""Report generation.

Reports are stored as **structured documents** — a list of typed sections — rather than
rendered HTML or a PDF blob.  One document can then be rendered in the app today and
exported to PDF later without regenerating it, and the same content is machine-readable
for anyone who wants the data instead of the layout.

Every number in a report comes from stored rows.  Nothing is recomputed at render time,
so a report shows what was true when it was generated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.tenancy import Role, TenantScope
from app.db.base import utcnow
from app.db.models.analysis import Analysis, PricingPlan, Product, Score
from app.db.models.competitor import Competitor
from app.db.models.enums import CompetitorStatus, JobStatus, ReportType, Severity
from app.db.models.monitoring import Change, Report
from app.services import comparison as comparison_service

log = get_logger(__name__)

# Section kinds the frontend renderer understands.  Adding one means teaching the
# renderer; the document format itself does not change.
SECTION_KINDS = ("text", "metrics", "table", "timeline", "list", "score_matrix")


def json_safe(value: Any) -> Any:
    """Make a document storable in JSONB.

    JSONB has no datetime, UUID or Decimal type, so those are converted here rather than
    at every call site.  Datetimes become ISO-8601 strings, which is also what the API
    returns, so the stored document and the rendered one agree.
    """
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _section(kind: str, title: str, **payload: Any) -> dict[str, Any]:
    if kind not in SECTION_KINDS:
        raise ValueError(f"Unknown section kind: {kind}")
    return {"kind": kind, "title": title, **payload}


async def generate(
    session: AsyncSession,
    scope: TenantScope,
    *,
    report_type: ReportType,
    competitor_ids: list[uuid.UUID] | None = None,
    period_days: int = 30,
    title: str | None = None,
) -> Report:
    """Generate a report synchronously.

    Synchronous on purpose: every builder reads already-stored data, so generation is
    measured in milliseconds. Nothing here crawls or calls a model, which is what makes a
    background job unnecessary.
    """
    scope.require(Role.VIEWER)
    period_end = utcnow()
    period_start = period_end - timedelta(days=max(1, min(period_days, 365)))

    builders = {
        ReportType.COMPETITOR_OVERVIEW: _build_competitor_overview,
        ReportType.COMPARISON: _build_comparison_report,
        ReportType.WEEKLY_INTELLIGENCE: _build_intelligence_report,
        ReportType.MONTHLY_COMPETITIVE: _build_intelligence_report,
    }
    builder = builders.get(report_type)
    if builder is None:
        raise ValidationError("Unsupported report type.", code="report_type_unsupported")

    report = Report(
        organization_id=scope.organization_id,
        created_by_user_id=scope.user_id,
        report_type=report_type,
        status=JobStatus.RUNNING,
        title=(title or _default_title(report_type)).strip()[:200],
        params={
            "competitor_ids": [str(cid) for cid in (competitor_ids or [])],
            "period_days": period_days,
        },
        period_start=period_start,
        period_end=period_end,
    )
    session.add(report)
    await session.flush()

    try:
        content = await builder(
            session,
            scope,
            competitor_ids=competitor_ids or [],
            period_start=period_start,
            period_end=period_end,
        )
    except (NotFoundError, ValidationError) as exc:
        report.status = JobStatus.FAILED
        report.error_message = exc.message
        raise

    report.content = json_safe(content)
    report.status = JobStatus.COMPLETED
    report.generated_at = utcnow()
    return report


def _default_title(report_type: ReportType) -> str:
    return {
        ReportType.COMPETITOR_OVERVIEW: "Competitor overview",
        ReportType.COMPARISON: "Competitor comparison",
        ReportType.WEEKLY_INTELLIGENCE: "Weekly competitive intelligence",
        ReportType.MONTHLY_COMPETITIVE: "Monthly competitive report",
    }[report_type]


async def _build_competitor_overview(
    session: AsyncSession,
    scope: TenantScope,
    *,
    competitor_ids: list[uuid.UUID],
    period_start,
    period_end,
) -> dict[str, Any]:
    if len(competitor_ids) != 1:
        raise ValidationError(
            "A competitor overview covers exactly one competitor.", code="report_needs_one"
        )
    competitor_id = competitor_ids[0]

    result = await session.execute(
        select(Competitor).where(
            Competitor.id == competitor_id,
            Competitor.organization_id == scope.organization_id,
            Competitor.deleted_at.is_(None),
        )
    )
    competitor = result.scalar_one_or_none()
    if competitor is None:
        raise NotFoundError("Competitor not found.", code="competitor_not_found")

    analysis_result = await session.execute(
        select(Analysis)
        .where(Analysis.competitor_id == competitor_id)
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    score_result = await session.execute(
        select(Score)
        .where(Score.competitor_id == competitor_id)
        .order_by(Score.created_at.desc())
        .limit(1)
    )
    score = score_result.scalar_one_or_none()

    products_result = await session.execute(
        select(Product).where(Product.competitor_id == competitor_id, Product.is_current.is_(True))
    )
    plans_result = await session.execute(
        select(PricingPlan).where(
            PricingPlan.competitor_id == competitor_id, PricingPlan.is_current.is_(True)
        )
    )
    changes_result = await session.execute(
        select(Change)
        .where(Change.competitor_id == competitor_id, Change.detected_at >= period_start)
        .order_by(Change.detected_at.desc())
        .limit(40)
    )

    sections: list[dict[str, Any]] = [
        _section(
            "metrics",
            "At a glance",
            metrics=[
                {
                    "label": "Overall score",
                    "value": score.overall if score else None,
                    "suffix": "/100",
                },
                {"label": "Threat level", "value": score.threat_level if score else "unknown"},
                {"label": "Pages analysed", "value": analysis.pages_analyzed if analysis else 0},
                {
                    "label": "Confidence",
                    "value": (
                        round(analysis.confidence * 100)
                        if analysis and analysis.confidence
                        else None
                    ),
                    "suffix": "%",
                },
            ],
        )
    ]

    if analysis:
        sections.append(
            _section(
                "text",
                "Summary",
                body=analysis.summary or "No summary was produced.",
                provenance="ai_inference",
                is_mock=analysis.is_mock,
            )
        )
        if analysis.strengths:
            sections.append(
                _section(
                    "list",
                    "Strengths",
                    items=[
                        {"title": item.get("title"), "detail": item.get("detail")}
                        for item in analysis.strengths
                    ],
                    provenance="ai_inference",
                )
            )
        if analysis.weaknesses:
            sections.append(
                _section(
                    "list",
                    "Weaknesses",
                    items=[
                        {"title": item.get("title"), "detail": item.get("detail")}
                        for item in analysis.weaknesses
                    ],
                    provenance="ai_inference",
                )
            )

    products = list(products_result.scalars().all())
    if products:
        sections.append(
            _section(
                "table",
                "Products",
                columns=["Product", "Category", "Features"],
                rows=[
                    [product.name, product.category or "—", ", ".join(product.features[:6]) or "—"]
                    for product in products
                ],
                provenance="ai_inference",
            )
        )

    plans = list(plans_result.scalars().all())
    if plans:
        sections.append(
            _section(
                "table",
                "Pricing",
                columns=["Plan", "Price", "Billing", "Source"],
                rows=[
                    [
                        plan.name,
                        "Custom"
                        if plan.is_custom_pricing
                        else "Free"
                        if plan.is_free
                        else f"{plan.currency or ''} {plan.amount}".strip()
                        if plan.amount is not None
                        else "Not published",
                        plan.billing_period.value,
                        plan.source.value,
                    ]
                    for plan in plans
                ],
                provenance="observed",
            )
        )

    changes = list(changes_result.scalars().all())
    if changes:
        sections.append(
            _section(
                "timeline",
                "Changes in this period",
                events=[
                    {
                        "at": change.detected_at,
                        "title": change.title,
                        "detail": change.description,
                        "severity": change.severity.value,
                        "type": change.change_type.value,
                    }
                    for change in changes
                ],
                provenance="observed",
            )
        )

    if score:
        sections.append(
            _section(
                "score_matrix",
                "Competitive score",
                dimensions=score.dimensions,
                overall=score.overall,
                methodology_version=score.methodology_version,
                confidence=score.confidence,
                provenance="observed",
            )
        )

    return {
        "subject": {
            "id": str(competitor.id),
            "name": competitor.name,
            "domain": competitor.domain,
            "website_url": competitor.website_url,
        },
        "period": {"start": period_start, "end": period_end},
        "generated_at": utcnow(),
        "is_mock": bool(analysis and analysis.is_mock),
        "sections": sections,
    }


async def _build_comparison_report(
    session: AsyncSession,
    scope: TenantScope,
    *,
    competitor_ids: list[uuid.UUID],
    period_start,
    period_end,
) -> dict[str, Any]:
    matrix = await comparison_service.build_matrix(session, scope, competitor_ids)

    dimension_rows = []
    names = [entry["name"] for entry in matrix["competitors"]]
    for dimension, values in matrix["dimensions"].items():
        dimension_rows.append(
            [dimension.replace("_", " ").title()]
            + [
                "Insufficient data" if values.get(name) is None else round(values[name])
                for name in names
            ]
        )
    dimension_rows.append(
        ["Overall"]
        + [
            "Insufficient data"
            if matrix["overall"].get(name) is None
            else round(matrix["overall"][name])
            for name in names
        ]
    )

    return {
        "subject": {"competitors": matrix["competitors"]},
        "period": {"start": period_start, "end": period_end},
        "generated_at": utcnow(),
        "is_mock": False,
        "sections": [
            _section(
                "table",
                "Score comparison",
                columns=["Dimension", *names],
                rows=dimension_rows,
                provenance="observed",
            ),
            _section(
                "text",
                "How to read this",
                body=(
                    "Scores are computed by a deterministic engine from data observed on each "
                    "competitor's public website, rounded to the nearest 5. "
                    '"Insufficient data" means the dimension could not be measured — it is not '
                    "a low score."
                ),
                provenance="observed",
            ),
        ],
    }


async def _build_intelligence_report(
    session: AsyncSession,
    scope: TenantScope,
    *,
    competitor_ids: list[uuid.UUID],
    period_start,
    period_end,
) -> dict[str, Any]:
    org_id = scope.organization_id

    changes_result = await session.execute(
        select(Change, Competitor.name)
        .join(Competitor, Competitor.id == Change.competitor_id)
        .where(Change.organization_id == org_id, Change.detected_at >= period_start)
        .order_by(Change.detected_at.desc())
        .limit(100)
    )
    rows = changes_result.all()

    severity_counts_result = await session.execute(
        select(Change.severity, func.count(Change.id))
        .where(Change.organization_id == org_id, Change.detected_at >= period_start)
        .group_by(Change.severity)
    )
    severity_counts = {severity.value: count for severity, count in severity_counts_result.all()}

    tracked_result = await session.execute(
        select(func.count(Competitor.id)).where(
            Competitor.organization_id == org_id,
            Competitor.deleted_at.is_(None),
            Competitor.status == CompetitorStatus.ACTIVE,
        )
    )

    sections: list[dict[str, Any]] = [
        _section(
            "metrics",
            "Activity in this period",
            metrics=[
                {"label": "Competitors tracked", "value": int(tracked_result.scalar_one() or 0)},
                {"label": "Changes detected", "value": len(rows)},
                {"label": "High severity", "value": severity_counts.get(Severity.HIGH.value, 0)},
                {
                    "label": "Medium severity",
                    "value": severity_counts.get(Severity.MEDIUM.value, 0),
                },
            ],
        )
    ]

    if rows:
        sections.append(
            _section(
                "timeline",
                "What changed",
                events=[
                    {
                        "at": change.detected_at,
                        "title": change.title,
                        "detail": change.description,
                        "severity": change.severity.value,
                        "type": change.change_type.value,
                        "competitor": name,
                    }
                    for change, name in rows
                ],
                provenance="observed",
            )
        )
    else:
        sections.append(
            _section(
                "text",
                "What changed",
                body="No changes were detected across your tracked competitors in this period.",
                provenance="observed",
            )
        )

    _ = competitor_ids
    return {
        "subject": {"organization_id": str(org_id)},
        "period": {"start": period_start, "end": period_end},
        "generated_at": utcnow(),
        "is_mock": False,
        "sections": sections,
    }


async def list_reports(
    session: AsyncSession, scope: TenantScope, *, limit: int = 25
) -> list[Report]:
    result = await session.execute(
        select(Report)
        .where(Report.organization_id == scope.organization_id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_report(session: AsyncSession, scope: TenantScope, report_id: uuid.UUID) -> Report:
    result = await session.execute(
        select(Report).where(
            Report.id == report_id, Report.organization_id == scope.organization_id
        )
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise NotFoundError("Report not found.", code="report_not_found")
    return report


async def delete_report(session: AsyncSession, scope: TenantScope, report_id: uuid.UUID) -> None:
    scope.require(Role.MEMBER)
    report = await get_report(session, scope, report_id)
    await session.delete(report)


__all__ = ["SECTION_KINDS", "delete_report", "generate", "get_report", "list_reports"]
