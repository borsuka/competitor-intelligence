"""AIService — the only AI entry point the rest of the application uses.

Responsibilities:

* choose the provider from configuration (and fall back to the labelled development
  provider when no key is set),
* route each prompt to the right model tier — extraction is reading comprehension and
  runs on the cheap model; synthesis and comparison run on the stronger one,
* enforce the guarantees that must not depend on the model behaving: a price that was
  never observed is discarded, not stored.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import AIProvider, AIResult, EmbeddingProvider, EmbeddingResult, PromptSpec
from app.ai.prompts import change_summary, comparison, extraction, positioning, recommendations
from app.ai.providers.embeddings import build_embedder
from app.ai.providers.mock_provider import MockProvider
from app.ai.schemas import (
    ChangeSummary,
    ComparisonInsights,
    ExtractedPricingPlan,
    ExtractionResult,
    PositioningResult,
    RecommendationResult,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.enums import AnalysisDepth

log = get_logger(__name__)

# Characters of page text sent per page, per depth.  The dial that actually controls
# cost: token spend is roughly linear in this number.
DEPTH_BUDGET: dict[AnalysisDepth, tuple[int, int]] = {
    # depth: (max pages fed to the model, max chars per page)
    AnalysisDepth.QUICK: (5, 3_000),
    AnalysisDepth.STANDARD: (12, 6_000),
    AnalysisDepth.DEEP: (25, 9_000),
}


def build_provider() -> AIProvider:
    """Instantiate the configured provider.

    A missing key is not an error: the development provider takes over, and everything it
    produces is flagged ``is_mock`` all the way through to the UI.
    """
    settings = get_settings()
    if settings.ai_provider == "anthropic" and settings.anthropic_api_key:
        from app.ai.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if settings.ai_provider == "anthropic":
        log.warning(
            "ai.provider.fallback",
            reason="AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is missing",
            using="mock",
        )
    return MockProvider()


def enforce_observed_prices(
    plans: list[ExtractedPricingPlan], observed_prices: list[dict[str, Any]]
) -> tuple[list[ExtractedPricingPlan], list[str]]:
    """Drop any price the crawler did not actually see on the page.

    This is the mechanical half of the no-fabricated-pricing guarantee.  The prompt asks
    the model not to invent amounts; this makes it impossible for an invented amount to
    reach the database regardless of what the model returns.

    Returns the corrected plans and a list of human-readable notes about what was removed.
    """
    allowed: set[tuple[float, str | None]] = set()
    allowed_amounts: set[float] = set()
    for price in observed_prices:
        amount = price.get("amount")
        if amount is None:
            continue
        allowed.add((round(float(amount), 2), (price.get("currency") or None)))
        allowed_amounts.add(round(float(amount), 2))

    corrected: list[ExtractedPricingPlan] = []
    notes: list[str] = []

    for plan in plans:
        if plan.amount is None:
            corrected.append(plan)
            continue

        amount = round(float(plan.amount), 2)
        if (amount, plan.currency) in allowed:
            corrected.append(plan)
            continue

        if amount in allowed_amounts:
            # Right number, wrong or missing currency: keep the amount, drop the currency
            # rather than asserting a currency nobody observed.
            corrected.append(plan.model_copy(update={"currency": None}))
            notes.append(f"Currency for plan '{plan.name}' was not observed and was cleared.")
            continue

        corrected.append(
            plan.model_copy(update={"amount": None, "currency": None, "is_custom_pricing": True})
        )
        notes.append(
            f"Price for plan '{plan.name}' was not found in the page text and was discarded."
        )

    return corrected, notes


class AIService:
    """Facade over the provider. Injectable, so tests never need a network call."""

    def __init__(
        self,
        provider: AIProvider | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._settings = get_settings()
        self._provider = provider or build_provider()
        self._embedder = embedder or build_embedder()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def is_mock(self) -> bool:
        return self._provider.is_mock

    def budget_for(self, depth: AnalysisDepth) -> tuple[int, int]:
        return DEPTH_BUDGET.get(depth, DEPTH_BUDGET[AnalysisDepth.STANDARD])

    async def _run(self, spec: PromptSpec, *, model: str) -> AIResult[Any]:
        result = await self._provider.complete_structured(
            spec, model=model, max_tokens=self._settings.ai_max_output_tokens
        )
        log.info(
            "ai.completed",
            prompt=spec.name,
            prompt_version=spec.version,
            provider=result.provider,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            duration_ms=result.duration_ms,
            is_mock=result.is_mock,
        )
        return result

    # ------------------------------------------------------------------ stages

    async def extract(
        self,
        *,
        company_name: str,
        pages: list[dict[str, Any]],
        observed_prices: list[dict[str, Any]],
        depth: AnalysisDepth = AnalysisDepth.STANDARD,
    ) -> tuple[AIResult[ExtractionResult], list[str]]:
        """Products, pricing and features. Returns the result plus correction notes."""
        max_pages, max_chars = self.budget_for(depth)
        spec = extraction.build(
            company_name=company_name,
            pages=pages[:max_pages],
            observed_prices=observed_prices,
            max_chars_per_page=max_chars,
        )
        result = await self._run(spec, model=self._settings.ai_model_extraction)

        corrected, notes = enforce_observed_prices(result.data.pricing_plans, observed_prices)
        if notes:
            log.warning(
                "ai.pricing_corrections",
                company=company_name,
                corrections=len(notes),
                provider=result.provider,
            )
        result.data = result.data.model_copy(update={"pricing_plans": corrected})
        return result, notes

    async def analyze_positioning(
        self,
        *,
        company_name: str,
        domain: str,
        pages: list[dict[str, Any]],
        extracted: ExtractionResult,
        external_links: list[str],
        depth: AnalysisDepth = AnalysisDepth.STANDARD,
    ) -> AIResult[PositioningResult]:
        max_pages, max_chars = self.budget_for(depth)
        spec = positioning.build(
            company_name=company_name,
            domain=domain,
            pages=pages[:max_pages],
            extracted_summary=_summarize_extraction(extracted),
            external_links=external_links,
            max_chars_per_page=max_chars,
        )
        return await self._run(spec, model=self._settings.ai_model_synthesis)

    async def recommend(
        self,
        *,
        own_company_name: str | None,
        own_company_description: str | None,
        competitor_name: str,
        analysis_summary: str,
    ) -> AIResult[RecommendationResult] | None:
        """Recommendations, or ``None`` when there is no own-company context.

        Without knowing the user's own company, any advice would be generic. Returning
        nothing lets the UI prompt the user to fill that in, which is more useful than a
        panel of platitudes.
        """
        if not own_company_name:
            return None
        spec = recommendations.build(
            own_company_name=own_company_name,
            own_company_description=own_company_description or "(no description provided)",
            competitor_name=competitor_name,
            analysis_summary=analysis_summary,
        )
        return await self._run(spec, model=self._settings.ai_model_synthesis)

    async def compare(
        self, *, competitors: list[dict[str, Any]], matrix: dict[str, Any]
    ) -> AIResult[ComparisonInsights]:
        spec = comparison.build(competitors=competitors, matrix=matrix)
        return await self._run(spec, model=self._settings.ai_model_synthesis)

    async def summarize_change(
        self, *, competitor_name: str, change: dict[str, Any]
    ) -> AIResult[ChangeSummary]:
        spec = change_summary.build(competitor_name=competitor_name, change=change)
        return await self._run(spec, model=self._settings.ai_model_extraction)

    # -------------------------------------------------------------- embeddings

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return await self._embedder.embed(texts)

    @property
    def embedding_provider_name(self) -> str:
        return self._embedder.name

    async def aclose(self) -> None:
        await self._provider.aclose()
        await self._embedder.aclose()


def _summarize_extraction(extracted: ExtractionResult) -> str:
    """Compact rendering of stage 1 output, fed into stage 2."""
    products = ", ".join(product.name for product in extracted.products[:10]) or "none extracted"
    plans = []
    for plan in extracted.pricing_plans[:10]:
        if plan.is_custom_pricing:
            plans.append(f"{plan.name}: custom pricing")
        elif plan.is_free:
            plans.append(f"{plan.name}: free")
        elif plan.amount is not None:
            currency = f"{plan.currency} " if plan.currency else ""
            plans.append(f"{plan.name}: {currency}{plan.amount} ({plan.billing_period.value})")
        else:
            plans.append(f"{plan.name}: price not published")
    features = ", ".join(extracted.key_features[:15]) or "none extracted"

    return (
        f"Products: {products}\n"
        f"Pricing: {'; '.join(plans) or 'none extracted'}\n"
        f"Key features: {features}"
    )


__all__ = ["DEPTH_BUDGET", "AIService", "build_provider", "enforce_observed_prices"]
