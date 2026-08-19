"""Development provider.

**This is not an AI.**  It derives structured output from the text that was actually
crawled, using deterministic heuristics, so the application can be developed, demoed and
tested end to end without an API key or a cent of spend.

Two rules keep it honest:

* Everything it produces is marked ``is_mock=True``.  That flag is stored on the analysis
  row, returned by the API and rendered as a persistent banner in the UI.
* It only ever restates what the crawler observed.  It does not invent a price, a
  customer, a statistic or a competitor claim — where it has nothing, it says so, exactly
  as the real pipeline does.

Selected automatically when ``AI_PROVIDER=mock`` (the default when no key is configured).
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from app.ai.base import AIResult, PromptSpec
from app.ai.schemas import (
    ChangeSummary,
    ComparisonInsights,
    ExtractedPricingPlan,
    ExtractedProduct,
    ExtractionResult,
    Insight,
    PositioningResult,
    Recommendation,
    RecommendationResult,
    SentimentResult,
)
from app.core.errors import AIResponseInvalidError
from app.db.models.enums import BillingPeriod

MOCK_MODEL = "development-heuristics-v1"

_PLAN_NAME_PATTERN = re.compile(
    r"\b(free|starter|basic|essential|standard|plus|pro|professional|team|business|"
    r"growth|premium|enterprise|scale|ultimate)\b",
    re.IGNORECASE,
)
_PERIOD_MAP = {
    "month": BillingPeriod.MONTHLY,
    "mo": BillingPeriod.MONTHLY,
    "monthly": BillingPeriod.MONTHLY,
    "year": BillingPeriod.YEARLY,
    "yr": BillingPeriod.YEARLY,
    "yearly": BillingPeriod.YEARLY,
    "annual": BillingPeriod.YEARLY,
    "annually": BillingPeriod.YEARLY,
    "one-time": BillingPeriod.ONE_TIME,
    "lifetime": BillingPeriod.ONE_TIME,
    "user": BillingPeriod.MONTHLY,
    "seat": BillingPeriod.MONTHLY,
}


class MockProvider:
    """Deterministic, offline, clearly-labelled stand-in for a real provider."""

    name = "mock"
    is_mock = True

    async def complete_structured(
        self,
        spec: PromptSpec,
        *,
        model: str,
        max_tokens: int,
    ) -> AIResult[Any]:
        started = time.perf_counter()
        context: dict[str, Any] = spec.metadata or {}

        builders = {
            "extraction": self._extraction,
            "positioning": self._positioning,
            "recommendations": self._recommendations,
            "comparison": self._comparison,
            "change_summary": self._change_summary,
            "sentiment": self._sentiment,
        }
        builder = builders.get(spec.name)
        if builder is None:
            raise AIResponseInvalidError(
                f"The development provider has no implementation for prompt '{spec.name}'.",
                code="mock_prompt_unsupported",
            )

        data = builder(context, spec.user)
        # Validate through the same schema the real provider is held to, so a schema
        # change breaks the mock in development instead of production.
        validated = spec.schema.model_validate(data.model_dump())

        return AIResult(
            data=validated,
            provider=self.name,
            model=MOCK_MODEL,
            tokens_in=len(spec.user) // 4,
            tokens_out=0,
            is_mock=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # ----------------------------------------------------------------- builders

    def _extraction(self, context: dict[str, Any], _user: str) -> ExtractionResult:
        observed_prices: list[dict[str, Any]] = context.get("observed_prices", [])
        headings: list[str] = context.get("headings", [])
        company = context.get("company_name", "This company")

        plans: list[ExtractedPricingPlan] = []
        seen_plans: set[str] = set()
        for price in observed_prices[:8]:
            label = self._plan_label(price) or f"Plan {len(plans) + 1}"
            if label.lower() in seen_plans:
                continue
            seen_plans.add(label.lower())
            period_token = (price.get("period") or "").lower()
            plans.append(
                ExtractedPricingPlan(
                    name=label,
                    amount=price.get("amount"),
                    currency=price.get("currency"),
                    billing_period=_PERIOD_MAP.get(period_token, BillingPeriod.UNKNOWN),
                    is_free=price.get("amount") == 0,
                    source_url=price.get("source_url"),
                )
            )

        products: list[ExtractedProduct] = []
        for heading in headings[:6]:
            if len(heading) < 3 or len(heading) > 120:
                continue
            products.append(
                ExtractedProduct(
                    name=heading[:200],
                    description=(
                        f"Heading observed on {company}'s site. "
                        "Development provider: no AI interpretation was applied."
                    ),
                    source_url=context.get("primary_url"),
                )
            )

        return ExtractionResult(
            products=products[:6],
            pricing_plans=plans,
            key_features=[h for h in headings[6:20] if 3 < len(h) < 120][:12],
            pricing_model_notes=(
                None if plans else "No prices were observed on the crawled pages."
            ),
            confidence=0.3 if plans or products else 0.1,
        )

    def _positioning(self, context: dict[str, Any], user: str) -> PositioningResult:
        company = context.get("company_name") or context.get("domain") or "This company"
        title = context.get("page_title") or ""
        description = context.get("meta_description") or ""
        ctas: list[str] = context.get("calls_to_action", [])
        word_count = int(context.get("word_count") or 0)

        summary_parts = [
            f"Development provider output for {company}.",
            f'The site\'s homepage title is "{title}".' if title else "",
            f'Its meta description reads: "{description}".' if description else "",
            f"{word_count} words of page text were collected across the crawled pages.",
            "No language model was used: this text restates crawler observations only.",
        ]
        summary = " ".join(part for part in summary_parts if part)

        strengths: list[Insight] = []
        if ctas:
            strengths.append(
                Insight(
                    title="Clear conversion paths",
                    detail=(
                        "The site presents explicit calls to action: " + ", ".join(ctas[:4]) + "."
                    ),
                )
            )
        if word_count > 1500:
            strengths.append(
                Insight(
                    title="Substantial site content",
                    detail=f"{word_count} words were collected, suggesting a well-developed site.",
                )
            )

        weaknesses: list[Insight] = []
        if not description:
            weaknesses.append(
                Insight(
                    title="Missing meta description",
                    detail="The homepage did not expose a meta description tag.",
                )
            )
        if word_count and word_count < 400:
            weaknesses.append(
                Insight(
                    title="Thin page content",
                    detail=f"Only {word_count} words were collected across the crawled pages.",
                )
            )

        return PositioningResult(
            company_summary=summary[:2000],
            target_audience=[],
            value_propositions=[description[:200]] if description else [],
            positioning_statement="",
            marketing_channels=context.get("marketing_channels", [])[:15],
            strengths=strengths,
            weaknesses=weaknesses,
            confidence=0.2,
            insufficient_data_for=[
                "target audience",
                "positioning statement",
                "competitive strengths and weaknesses",
            ],
        )

    def _recommendations(self, context: dict[str, Any], _user: str) -> RecommendationResult:
        # Deliberately empty: strategic advice is exactly the thing that must not be
        # invented by a heuristic. The UI shows "configure an AI provider" instead.
        return RecommendationResult(recommendations=self._placeholder_recommendations(context))

    @staticmethod
    def _placeholder_recommendations(context: dict[str, Any]) -> list[Recommendation]:
        if not context.get("include_placeholder"):
            return []
        return [
            Recommendation(
                title="Configure an AI provider to generate recommendations",
                rationale=(
                    "Strategic recommendations require a language model. The development "
                    "provider does not generate them, because inventing strategy from "
                    "heuristics would be indistinguishable from guessing."
                ),
                priority="medium",
                effort="low",
            )
        ]

    def _comparison(self, context: dict[str, Any], _user: str) -> ComparisonInsights:
        names: list[str] = context.get("competitor_names", [])
        scores: dict[str, float | None] = context.get("overall_scores", {})
        scored = {name: value for name, value in scores.items() if value is not None}
        strongest = max(scored, key=lambda n: scored[n]) if scored else None

        return ComparisonInsights(
            summary=(
                "Development provider output. The score matrix below is computed by the "
                "deterministic scoring engine from observed data; the narrative that a "
                "language model would normally add is not available without an AI "
                f"provider. Competitors compared: {', '.join(names[:8]) or 'none'}."
            ),
            strongest_competitor=strongest,
            strongest_reason=(
                f"Highest overall score ({scored[strongest]:.0f}) in the deterministic matrix."
                if strongest
                else None
            ),
            differentiation_opportunities=[],
        )

    def _change_summary(self, context: dict[str, Any], _user: str) -> ChangeSummary:
        return ChangeSummary(
            headline=str(context.get("headline") or "Change detected")[:200],
            explanation=str(context.get("explanation") or "")[:800],
        )

    def _sentiment(self, _context: dict[str, Any], _user: str) -> SentimentResult:
        # No review source is configured, so there is nothing to analyse and nothing is
        # invented.
        return SentimentResult(overall_sentiment="unknown", review_count_analyzed=0)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _plan_label(price: dict[str, Any]) -> str | None:
        """Name a plan from the words closest to its price.

        The *last* keyword before the amount, not the first in the window: on a pricing
        page every tier sits within a hundred characters of the next, so taking the first
        match would label every price "Free".
        """
        preceding = str(price.get("preceding") or "")
        matches = list(_PLAN_NAME_PATTERN.finditer(preceding))
        if matches:
            return matches[-1].group(0).title()
        match = _PLAN_NAME_PATTERN.search(str(price.get("context") or ""))
        return match.group(0).title() if match else None

    async def aclose(self) -> None:
        return None


def deterministic_seed(text: str) -> int:
    """Stable seed derived from content, so mock output does not change between runs."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


__all__ = ["MOCK_MODEL", "MockProvider", "deterministic_seed"]
