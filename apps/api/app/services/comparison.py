"""Multi-competitor comparison.

The score matrix is computed from stored scores — no model involved — so the table is
reproducible and auditable.  The AI narrative is layered on top and is clearly separated
in the response: ``matrix`` is data, ``insights`` is interpretation.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import AIService
from app.core.errors import AIProviderError, ValidationError
from app.core.logging import get_logger
from app.core.tenancy import Role, TenantScope
from app.db.models.analysis import Analysis, PricingPlan, Product, Score
from app.db.models.competitor import Competitor
from app.db.models.monitoring import Comparison
from app.services.scoring import DIMENSION_WEIGHTS

log = get_logger(__name__)

MIN_COMPETITORS = 2
MAX_COMPETITORS = 6


async def build_matrix(
    session: AsyncSession, scope: TenantScope, competitor_ids: list[uuid.UUID]
) -> dict[str, Any]:
    """Deterministic score matrix for the selected competitors.

    Loads competitors and their latest scores in two queries rather than one per
    competitor — this endpoint is called with up to six ids and is on the critical path
    for the comparison page.
    """
    if not MIN_COMPETITORS <= len(competitor_ids) <= MAX_COMPETITORS:
        raise ValidationError(
            f"Select between {MIN_COMPETITORS} and {MAX_COMPETITORS} competitors to compare.",
            code="comparison_size",
        )

    unique_ids = list(dict.fromkeys(competitor_ids))
    result = await session.execute(
        select(Competitor).where(
            Competitor.id.in_(unique_ids),
            Competitor.organization_id == scope.organization_id,
            Competitor.deleted_at.is_(None),
        )
    )
    competitors = list(result.scalars().all())
    if len(competitors) != len(unique_ids):
        # Silently comparing a subset would show a table the user did not ask for.
        raise ValidationError(
            "One or more of the selected competitors could not be found.",
            code="competitor_not_found",
        )

    scores_result = await session.execute(
        select(Score)
        .where(Score.competitor_id.in_(unique_ids))
        .distinct(Score.competitor_id)
        .order_by(Score.competitor_id, Score.created_at.desc())
    )
    scores_by_competitor = {score.competitor_id: score for score in scores_result.scalars().all()}

    dimensions: dict[str, dict[str, float | None]] = {}
    for dimension in DIMENSION_WEIGHTS:
        row: dict[str, float | None] = {}
        for competitor in competitors:
            score = scores_by_competitor.get(competitor.id)
            entry = (score.dimensions or {}).get(dimension.value) if score else None
            row[competitor.name] = entry.get("score") if isinstance(entry, dict) else None
        dimensions[dimension.value] = row

    overall = {
        competitor.name: (
            scores_by_competitor[competitor.id].overall
            if competitor.id in scores_by_competitor
            else None
        )
        for competitor in competitors
    }

    return {
        "competitors": [
            {
                "id": str(competitor.id),
                "name": competitor.name,
                "domain": competitor.domain,
                "favicon_url": competitor.favicon_url,
                "threat_level": (
                    scores_by_competitor[competitor.id].threat_level
                    if competitor.id in scores_by_competitor
                    else "unknown"
                ),
                "confidence": (
                    scores_by_competitor[competitor.id].confidence
                    if competitor.id in scores_by_competitor
                    else None
                ),
                "analyzed": competitor.id in scores_by_competitor,
            }
            for competitor in competitors
        ],
        "dimensions": dimensions,
        "overall": overall,
        "methodology_version": next(
            (score.methodology_version for score in scores_by_competitor.values()),
            None,
        ),
    }


async def create_comparison(
    session: AsyncSession,
    scope: TenantScope,
    *,
    competitor_ids: list[uuid.UUID],
    name: str | None = None,
    ai: AIService | None = None,
    with_insights: bool = True,
) -> Comparison:
    """Build the matrix, optionally add the AI narrative, and store the result.

    A failure in the AI stage does not fail the comparison: the matrix is the substance,
    the narrative is a bonus, and losing the table because a provider timed out would be
    the wrong trade.
    """
    scope.require(Role.MEMBER)
    matrix = await build_matrix(session, scope, competitor_ids)

    comparison = Comparison(
        organization_id=scope.organization_id,
        created_by_user_id=scope.user_id,
        name=(name or "").strip()[:160] or None,
        competitor_ids=list(dict.fromkeys(competitor_ids)),
        matrix=matrix,
    )

    if with_insights:
        ai = ai or AIService()
        try:
            profiles = await _load_profiles(session, scope, competitor_ids, matrix)
            insights = await ai.compare(competitors=profiles, matrix=matrix)
            comparison.insights = insights.data.model_dump()
            comparison.provider = insights.provider
            comparison.is_mock = insights.is_mock
        except AIProviderError as exc:
            log.warning("comparison.insights_failed", error=str(exc)[:200])
            comparison.insights = None

    session.add(comparison)
    await session.flush()
    return comparison


async def _load_profiles(
    session: AsyncSession,
    scope: TenantScope,
    competitor_ids: list[uuid.UUID],
    matrix: dict[str, Any],
) -> list[dict[str, Any]]:
    """Assemble the per-competitor context for the comparison prompt.

    Three queries total, regardless of how many competitors are being compared.
    """
    analyses_result = await session.execute(
        select(Analysis)
        .where(
            Analysis.competitor_id.in_(competitor_ids),
            Analysis.organization_id == scope.organization_id,
        )
        .distinct(Analysis.competitor_id)
        .order_by(Analysis.competitor_id, Analysis.created_at.desc())
    )
    analyses = {analysis.competitor_id: analysis for analysis in analyses_result.scalars().all()}

    products_result = await session.execute(
        select(Product).where(
            Product.competitor_id.in_(competitor_ids), Product.is_current.is_(True)
        )
    )
    products: dict[uuid.UUID, list[str]] = {}
    for product in products_result.scalars().all():
        products.setdefault(product.competitor_id, []).append(product.name)

    plans_result = await session.execute(
        select(PricingPlan).where(
            PricingPlan.competitor_id.in_(competitor_ids), PricingPlan.is_current.is_(True)
        )
    )
    pricing: dict[uuid.UUID, list[str]] = {}
    for plan in plans_result.scalars().all():
        if plan.is_custom_pricing:
            rendered = f"{plan.name}: custom"
        elif plan.amount is not None:
            rendered = (
                f"{plan.name}: {plan.currency or ''}{plan.amount} / {plan.billing_period.value}"
            )
        else:
            rendered = f"{plan.name}: unpublished"
        pricing.setdefault(plan.competitor_id, []).append(rendered)

    profiles: list[dict[str, Any]] = []
    for entry in matrix["competitors"]:
        competitor_id = uuid.UUID(entry["id"])
        analysis = analyses.get(competitor_id)
        profiles.append(
            {
                "name": entry["name"],
                "domain": entry["domain"],
                "summary": analysis.summary if analysis else None,
                "products": products.get(competitor_id, [])[:10],
                "pricing_text": "; ".join(pricing.get(competitor_id, [])[:8]),
                "strengths": [
                    item.get("title", "") for item in (analysis.strengths if analysis else [])
                ][:5],
                "weaknesses": [
                    item.get("title", "") for item in (analysis.weaknesses if analysis else [])
                ][:5],
                "overall_score": matrix["overall"].get(entry["name"]),
            }
        )
    return profiles


async def list_comparisons(
    session: AsyncSession, scope: TenantScope, *, limit: int = 20
) -> list[Comparison]:
    result = await session.execute(
        select(Comparison)
        .where(Comparison.organization_id == scope.organization_id)
        .order_by(Comparison.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_comparison(
    session: AsyncSession, scope: TenantScope, comparison_id: uuid.UUID
) -> Comparison:
    from app.core.errors import NotFoundError

    result = await session.execute(
        select(Comparison).where(
            Comparison.id == comparison_id,
            Comparison.organization_id == scope.organization_id,
        )
    )
    comparison = result.scalar_one_or_none()
    if comparison is None:
        raise NotFoundError("Comparison not found.", code="comparison_not_found")
    return comparison


__all__ = [
    "MAX_COMPETITORS",
    "MIN_COMPETITORS",
    "build_matrix",
    "create_comparison",
    "get_comparison",
    "list_comparisons",
]
