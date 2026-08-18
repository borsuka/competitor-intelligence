"""Schemas every AI response must satisfy.

The application never consumes free-form model text.  A provider returns JSON, that JSON
is validated against one of these models, and a response that does not validate is an
error — not something to paper over.

Field constraints do real work here: they are the last line of defence against a model
(or an injected page) inventing a price, a 400-item feature list, or a confidence of 1.0
for a page it could not read.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.enums import BillingPeriod


class StrictModel(BaseModel):
    """Base with extra fields forbidden.

    If a provider starts returning a new field we want to know, not silently ignore it —
    an unexpected key is usually a prompt or schema drift bug.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(StrictModel):
    """Where a claim came from. Empty evidence is allowed; a fabricated URL is not."""

    quote: str | None = Field(default=None, max_length=400)
    source_url: str | None = Field(default=None, max_length=2048)


class Insight(StrictModel):
    title: str = Field(min_length=2, max_length=160)
    detail: str = Field(min_length=2, max_length=800)
    evidence: Evidence | None = None


class ExtractedProduct(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    features: list[str] = Field(default_factory=list, max_length=25)
    source_url: str | None = Field(default=None, max_length=2048)

    @field_validator("features")
    @classmethod
    def _trim_features(cls, value: list[str]) -> list[str]:
        return [item[:200] for item in value if item and item.strip()]


class ExtractedPricingPlan(StrictModel):
    """A pricing tier.

    ``amount`` is optional on purpose.  The service additionally refuses any amount that
    does not appear in the observed prices scraped from the page, so a model that
    hallucinates "€99" for a plan whose page says "Contact us" gets the amount dropped
    rather than stored.
    """

    name: str = Field(min_length=1, max_length=120)
    amount: float | None = Field(default=None, ge=0, le=10_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    billing_period: BillingPeriod = BillingPeriod.UNKNOWN
    is_custom_pricing: bool = False
    is_free: bool = False
    features: list[str] = Field(default_factory=list, max_length=40)
    highlights: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2048)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().upper()
        return candidate if candidate.isalpha() else None


class ExtractionResult(StrictModel):
    """Stage 1: what does this company sell, and for how much."""

    products: list[ExtractedProduct] = Field(default_factory=list, max_length=40)
    pricing_plans: list[ExtractedPricingPlan] = Field(default_factory=list, max_length=20)
    key_features: list[str] = Field(default_factory=list, max_length=40)
    pricing_model_notes: str | None = Field(default=None, max_length=800)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class PositioningResult(StrictModel):
    """Stage 2: how does this company present itself, and where is it strong or weak."""

    company_summary: str = Field(min_length=20, max_length=2000)
    target_audience: list[str] = Field(default_factory=list, max_length=10)
    value_propositions: list[str] = Field(default_factory=list, max_length=10)
    positioning_statement: str = Field(default="", max_length=800)
    marketing_channels: list[str] = Field(default_factory=list, max_length=15)
    strengths: list[Insight] = Field(default_factory=list, max_length=8)
    weaknesses: list[Insight] = Field(default_factory=list, max_length=8)
    tone_of_voice: str | None = Field(default=None, max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # The model's own statement of what it could not determine.  Displayed verbatim: an
    # honest gap is more useful than a confident guess.
    insufficient_data_for: list[str] = Field(default_factory=list, max_length=10)


class Recommendation(StrictModel):
    title: str = Field(min_length=3, max_length=160)
    rationale: str = Field(min_length=10, max_length=800)
    priority: Literal["low", "medium", "high"] = "medium"
    effort: Literal["low", "medium", "high"] = "medium"


class RecommendationResult(StrictModel):
    """Stage 3: what the user's own company should consider doing about it."""

    recommendations: list[Recommendation] = Field(default_factory=list, max_length=8)


class ComparisonInsights(StrictModel):
    """Cross-competitor narrative that accompanies the deterministic score matrix."""

    summary: str = Field(min_length=20, max_length=2000)
    strongest_competitor: str | None = Field(default=None, max_length=160)
    strongest_reason: str | None = Field(default=None, max_length=600)
    biggest_threat: str | None = Field(default=None, max_length=160)
    biggest_threat_reason: str | None = Field(default=None, max_length=600)
    biggest_opportunity: str | None = Field(default=None, max_length=600)
    weakest_area_across_market: str | None = Field(default=None, max_length=400)
    differentiation_opportunities: list[str] = Field(default_factory=list, max_length=8)


class ChangeSummary(StrictModel):
    """A human sentence for a diff the change detector already computed."""

    headline: str = Field(min_length=5, max_length=200)
    explanation: str = Field(default="", max_length=800)
    why_it_matters: str | None = Field(default=None, max_length=600)


class SentimentResult(StrictModel):
    """Review sentiment. Only produced when review data actually exists."""

    overall_sentiment: Literal["positive", "mixed", "negative", "unknown"] = "unknown"
    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    positive_themes: list[str] = Field(default_factory=list, max_length=10)
    complaints: list[str] = Field(default_factory=list, max_length=10)
    review_count_analyzed: int = Field(default=0, ge=0)


__all__ = [
    "StrictModel",
    "Evidence",
    "Insight",
    "ExtractedProduct",
    "ExtractedPricingPlan",
    "ExtractionResult",
    "PositioningResult",
    "Recommendation",
    "RecommendationResult",
    "ComparisonInsights",
    "ChangeSummary",
    "SentimentResult",
]
