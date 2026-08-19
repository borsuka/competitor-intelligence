"""Competitive scoring.

Deterministic, inspectable, and deliberately imprecise.

Three rules shape this module:

1. **No AI in the number.** Every dimension is computed from counted, observed inputs. The
   same competitor data always produces the same score, and a user can reproduce it.
2. **Missing data is missing, not zero.** A dimension with nothing to measure scores
   ``None`` and is excluded from the mean. Scoring an unmeasured dimension as 0 would make
   "we did not crawl their pricing page" indistinguishable from "their pricing is bad".
3. **No false precision.** Scores are rounded to the nearest 5 and shipped with a
   confidence and a completeness ratio, because the underlying signal does not justify
   "87.3".

Every dimension records the inputs it used and a sentence explaining the result, so the UI
can answer "why this score?" from stored data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models.enums import ScoreDimension

METHODOLOGY_VERSION = "1.0.0"

# Weights sum to 1.0 over the dimensions that have data; missing ones are renormalised.
DIMENSION_WEIGHTS: dict[ScoreDimension, float] = {
    ScoreDimension.PRODUCT: 0.18,
    ScoreDimension.PRICING: 0.16,
    ScoreDimension.FEATURES: 0.14,
    ScoreDimension.POSITIONING: 0.14,
    ScoreDimension.SEO: 0.12,
    ScoreDimension.MARKETING: 0.12,
    ScoreDimension.BRAND: 0.09,
    ScoreDimension.SENTIMENT: 0.05,
}


@dataclass(slots=True)
class ScoringInput:
    """Everything the scorer needs, as plain values.

    A dataclass rather than ORM objects so the engine can be unit-tested without a
    database and reused from a script.
    """

    product_count: int = 0
    products_with_description: int = 0
    feature_count: int = 0
    features_per_product: float = 0.0

    pricing_plan_count: int = 0
    plans_with_public_amount: int = 0
    has_free_tier: bool = False
    has_custom_pricing: bool = False
    pricing_page_found: bool = False

    value_proposition_count: int = 0
    has_positioning_statement: bool = False
    target_audience_count: int = 0
    ai_confidence: float | None = None

    pages_crawled: int = 0
    pages_missing_title: int = 0
    pages_missing_meta_description: int = 0
    pages_missing_h1: int = 0
    has_sitemap: bool = False
    has_robots_txt: bool = False
    has_blog: bool = False
    structured_data_type_count: int = 0
    avg_word_count: float = 0.0
    internal_link_count: int = 0

    marketing_channel_count: int = 0
    social_profile_count: int = 0
    case_study_pages: int = 0
    cta_count: int = 0

    open_graph_tag_count: int = 0
    has_favicon: bool = False
    external_link_count: int = 0

    review_count: int = 0
    sentiment_score: float | None = None


@dataclass(slots=True)
class DimensionScore:
    dimension: ScoreDimension
    score: float | None
    weight: float
    rationale: str
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "weight": self.weight,
            "rationale": self.rationale,
            "inputs": self.inputs,
        }


@dataclass(slots=True)
class ScoreResult:
    overall: float | None
    dimensions: dict[str, dict[str, Any]]
    confidence: float
    data_completeness: float
    threat_level: str
    methodology_version: str = METHODOLOGY_VERSION


def _bounded(value: float, *, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _scale(value: float, *, best: float, floor: float = 0.0, ceiling: float = 100.0) -> float:
    """Map a count onto a 0-100 range with diminishing returns.

    Linear-to-a-cap: the tenth product tells us much less than the second, and a site with
    200 pages is not twenty times more formidable than one with ten.
    """
    if best <= 0:
        return floor
    ratio = min(1.0, value / best)
    return _bounded(floor + (ceiling - floor) * ratio)


def score_product(data: ScoringInput) -> DimensionScore:
    inputs = {
        "product_count": data.product_count,
        "products_with_description": data.products_with_description,
        "features_per_product": round(data.features_per_product, 2),
    }
    if data.product_count == 0:
        return DimensionScore(
            ScoreDimension.PRODUCT,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.PRODUCT],
            "No products or services could be identified from the crawled pages.",
            inputs,
        )

    breadth = _scale(data.product_count, best=8)
    described = (
        (data.products_with_description / data.product_count) * 100 if data.product_count else 0
    )
    depth = _scale(data.features_per_product, best=6)
    score = 0.5 * breadth + 0.25 * described + 0.25 * depth

    return DimensionScore(
        ScoreDimension.PRODUCT,
        _bounded(score),
        DIMENSION_WEIGHTS[ScoreDimension.PRODUCT],
        f"{data.product_count} product(s) identified, "
        f"{data.products_with_description} with a description, "
        f"averaging {data.features_per_product:.1f} features each.",
        inputs,
    )


def score_pricing(data: ScoringInput) -> DimensionScore:
    """Scores pricing *transparency and structure*, not cheapness.

    Whether a price is "good" depends on the buyer; whether it is published, tiered and
    comprehensible is observable and is what competitors actually get judged on.
    """
    inputs = {
        "pricing_page_found": data.pricing_page_found,
        "pricing_plan_count": data.pricing_plan_count,
        "plans_with_public_amount": data.plans_with_public_amount,
        "has_free_tier": data.has_free_tier,
        "has_custom_pricing": data.has_custom_pricing,
    }
    if not data.pricing_page_found and data.pricing_plan_count == 0:
        return DimensionScore(
            ScoreDimension.PRICING,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.PRICING],
            "No pricing page or pricing information was found.",
            inputs,
        )

    transparency = (
        (data.plans_with_public_amount / data.pricing_plan_count) * 100
        if data.pricing_plan_count
        else 0.0
    )
    structure = _scale(data.pricing_plan_count, best=4)
    accessibility = 0.0
    if data.has_free_tier:
        accessibility += 60.0
    if data.has_custom_pricing:
        accessibility += 40.0  # an enterprise motion is a real market position

    score = 0.45 * transparency + 0.35 * structure + 0.20 * min(accessibility, 100.0)

    return DimensionScore(
        ScoreDimension.PRICING,
        _bounded(score),
        DIMENSION_WEIGHTS[ScoreDimension.PRICING],
        f"{data.pricing_plan_count} plan(s) found, "
        f"{data.plans_with_public_amount} with a published price"
        + (", free tier available" if data.has_free_tier else "")
        + (", custom/enterprise pricing offered" if data.has_custom_pricing else "")
        + ".",
        inputs,
    )


def score_features(data: ScoringInput) -> DimensionScore:
    inputs = {"feature_count": data.feature_count}
    if data.feature_count == 0:
        return DimensionScore(
            ScoreDimension.FEATURES,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.FEATURES],
            "No product capabilities were identified on the crawled pages.",
            inputs,
        )
    score = _scale(data.feature_count, best=25)
    return DimensionScore(
        ScoreDimension.FEATURES,
        score,
        DIMENSION_WEIGHTS[ScoreDimension.FEATURES],
        f"{data.feature_count} distinct capabilities were described across the site.",
        inputs,
    )


def score_positioning(data: ScoringInput) -> DimensionScore:
    inputs = {
        "value_proposition_count": data.value_proposition_count,
        "has_positioning_statement": data.has_positioning_statement,
        "target_audience_count": data.target_audience_count,
        "ai_confidence": data.ai_confidence,
    }
    signals = (
        data.value_proposition_count
        + data.target_audience_count
        + (1 if data.has_positioning_statement else 0)
    )
    if signals == 0:
        return DimensionScore(
            ScoreDimension.POSITIONING,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.POSITIONING],
            "The site did not communicate a discernible positioning.",
            inputs,
        )

    clarity = _scale(data.value_proposition_count, best=4)
    audience = _scale(data.target_audience_count, best=3)
    statement = 100.0 if data.has_positioning_statement else 0.0
    score = 0.4 * clarity + 0.3 * audience + 0.3 * statement

    return DimensionScore(
        ScoreDimension.POSITIONING,
        _bounded(score),
        DIMENSION_WEIGHTS[ScoreDimension.POSITIONING],
        f"{data.value_proposition_count} value proposition(s) and "
        f"{data.target_audience_count} audience segment(s) were identifiable"
        + (", with an explicit positioning statement" if data.has_positioning_statement else "")
        + ".",
        inputs,
    )


def score_seo(data: ScoringInput) -> DimensionScore:
    """On-page SEO hygiene only.

    Rankings, traffic and backlinks need a third-party tool we do not have, so they are
    not estimated here.  Reporting a "domain authority" we cannot observe would be exactly
    the kind of invented precision this product must avoid.
    """
    inputs = {
        "pages_crawled": data.pages_crawled,
        "pages_missing_title": data.pages_missing_title,
        "pages_missing_meta_description": data.pages_missing_meta_description,
        "pages_missing_h1": data.pages_missing_h1,
        "has_sitemap": data.has_sitemap,
        "has_robots_txt": data.has_robots_txt,
        "has_blog": data.has_blog,
        "structured_data_type_count": data.structured_data_type_count,
        "avg_word_count": round(data.avg_word_count, 1),
    }
    if data.pages_crawled == 0:
        return DimensionScore(
            ScoreDimension.SEO,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.SEO],
            "No pages were successfully crawled.",
            inputs,
        )

    crawled = data.pages_crawled
    titles = (1 - data.pages_missing_title / crawled) * 100
    metas = (1 - data.pages_missing_meta_description / crawled) * 100
    h1s = (1 - data.pages_missing_h1 / crawled) * 100
    technical = (
        (40.0 if data.has_sitemap else 0.0)
        + (20.0 if data.has_robots_txt else 0.0)
        + min(40.0, data.structured_data_type_count * 13.0)
    )
    content = 0.5 * _scale(data.avg_word_count, best=900) + 0.5 * (100.0 if data.has_blog else 0.0)

    score = 0.25 * titles + 0.20 * metas + 0.15 * h1s + 0.20 * technical + 0.20 * content

    missing = []
    if data.pages_missing_title:
        missing.append(f"{data.pages_missing_title} without a title")
    if data.pages_missing_meta_description:
        missing.append(f"{data.pages_missing_meta_description} without a meta description")
    if data.pages_missing_h1:
        missing.append(f"{data.pages_missing_h1} without an H1")

    return DimensionScore(
        ScoreDimension.SEO,
        _bounded(score),
        DIMENSION_WEIGHTS[ScoreDimension.SEO],
        f"Across {crawled} crawled page(s): "
        + (", ".join(missing) if missing else "titles, meta descriptions and H1s present")
        + (". Sitemap found" if data.has_sitemap else ". No sitemap found")
        + (", blog present" if data.has_blog else "")
        + ". Rankings and backlinks are not measured.",
        inputs,
    )


def score_marketing(data: ScoringInput) -> DimensionScore:
    inputs = {
        "marketing_channel_count": data.marketing_channel_count,
        "social_profile_count": data.social_profile_count,
        "case_study_pages": data.case_study_pages,
        "cta_count": data.cta_count,
        "has_blog": data.has_blog,
    }
    signals = (
        data.marketing_channel_count
        + data.social_profile_count
        + data.case_study_pages
        + data.cta_count
    )
    if signals == 0:
        return DimensionScore(
            ScoreDimension.MARKETING,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.MARKETING],
            "No marketing activity was visible from the crawled pages.",
            inputs,
        )

    channels = _scale(data.marketing_channel_count, best=5)
    social = _scale(data.social_profile_count, best=4)
    proof = _scale(data.case_study_pages, best=3)
    conversion = _scale(data.cta_count, best=5)
    content = 100.0 if data.has_blog else 0.0
    score = 0.25 * channels + 0.2 * social + 0.2 * proof + 0.2 * conversion + 0.15 * content

    return DimensionScore(
        ScoreDimension.MARKETING,
        _bounded(score),
        DIMENSION_WEIGHTS[ScoreDimension.MARKETING],
        f"{data.social_profile_count} social profile(s), "
        f"{data.case_study_pages} case study page(s) and "
        f"{data.cta_count} distinct call(s) to action were observed"
        + (", plus an active blog" if data.has_blog else "")
        + ".",
        inputs,
    )


def score_brand(data: ScoringInput) -> DimensionScore:
    inputs = {
        "open_graph_tag_count": data.open_graph_tag_count,
        "has_favicon": data.has_favicon,
        "external_link_count": data.external_link_count,
        "structured_data_type_count": data.structured_data_type_count,
    }
    if data.pages_crawled == 0:
        return DimensionScore(
            ScoreDimension.BRAND,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.BRAND],
            "No pages were successfully crawled.",
            inputs,
        )

    presentation = _scale(data.open_graph_tag_count, best=6)
    identity = 100.0 if data.has_favicon else 0.0
    ecosystem = _scale(data.external_link_count, best=25)
    markup = _scale(data.structured_data_type_count, best=3)
    score = 0.35 * presentation + 0.2 * identity + 0.25 * ecosystem + 0.2 * markup

    return DimensionScore(
        ScoreDimension.BRAND,
        _bounded(score),
        DIMENSION_WEIGHTS[ScoreDimension.BRAND],
        f"{data.open_graph_tag_count} Open Graph tag(s) and "
        f"{data.structured_data_type_count} structured-data type(s) present"
        + ("; site favicon detected" if data.has_favicon else "; no favicon detected")
        + ". Brand strength beyond the site itself is not measured.",
        inputs,
    )


def score_sentiment(data: ScoringInput) -> DimensionScore:
    """Customer sentiment.

    No review source is configured in this deployment, so this dimension normally returns
    ``None`` — "Insufficient data" — rather than a fabricated number.  The calculation is
    implemented so that connecting a review provider makes it work without changes here.
    """
    inputs = {"review_count": data.review_count, "sentiment_score": data.sentiment_score}
    if data.review_count == 0 or data.sentiment_score is None:
        return DimensionScore(
            ScoreDimension.SENTIMENT,
            None,
            DIMENSION_WEIGHTS[ScoreDimension.SENTIMENT],
            "No review data is available. Connect a review source to score this dimension.",
            inputs,
        )
    # sentiment_score is -1..1; map onto 0..100.
    score = _bounded((data.sentiment_score + 1.0) * 50.0)
    return DimensionScore(
        ScoreDimension.SENTIMENT,
        score,
        DIMENSION_WEIGHTS[ScoreDimension.SENTIMENT],
        f"Derived from {data.review_count} review(s).",
        inputs,
    )


_SCORERS = (
    score_product,
    score_pricing,
    score_features,
    score_positioning,
    score_seo,
    score_marketing,
    score_brand,
    score_sentiment,
)


def threat_level_for(overall: float | None, completeness: float) -> str:
    """Coarse label for the dashboard.

    Four buckets, not a number: "threat 63/100" implies a precision that a website crawl
    cannot support.  Below half-complete data the label is withheld entirely.
    """
    if overall is None or completeness < 0.5:
        return "unknown"
    if overall >= 80:
        return "critical"
    if overall >= 60:
        return "high"
    if overall >= 40:
        return "moderate"
    return "low"


def compute_score(data: ScoringInput) -> ScoreResult:
    """Run every dimension and combine those that have data."""
    scored = [scorer(data) for scorer in _SCORERS]
    with_data = [dimension for dimension in scored if dimension.has_data]

    total_weight = sum(dimension.weight for dimension in with_data)
    if total_weight > 0:
        weighted = sum((dimension.score or 0.0) * dimension.weight for dimension in with_data)
        # Round to the nearest 5: the inputs do not justify single-point resolution.
        overall: float | None = round(weighted / total_weight / 5.0) * 5.0
    else:
        overall = None

    completeness = len(with_data) / len(scored) if scored else 0.0

    # Confidence combines how much of the model we could fill in with how much the AI
    # stage trusted its own reading.
    confidence = completeness
    if data.ai_confidence is not None:
        confidence = 0.6 * completeness + 0.4 * data.ai_confidence
    if data.pages_crawled < 3:
        confidence *= 0.7  # one or two pages is not a picture of a company

    return ScoreResult(
        overall=overall,
        dimensions={dimension.dimension.value: dimension.to_dict() for dimension in scored},
        confidence=round(_bounded(confidence, low=0.0, high=1.0), 2),
        data_completeness=round(completeness, 2),
        threat_level=threat_level_for(overall, completeness),
    )


__all__ = [
    "DIMENSION_WEIGHTS",
    "METHODOLOGY_VERSION",
    "DimensionScore",
    "ScoreResult",
    "ScoringInput",
    "compute_score",
    "threat_level_for",
]
