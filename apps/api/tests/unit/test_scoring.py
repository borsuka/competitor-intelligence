"""Scoring engine.

The properties worth protecting are not the exact numbers — those are a judgement call —
but the invariants that keep the numbers honest: missing data must not read as bad data,
and the result must be reproducible.
"""

from __future__ import annotations

from app.services.scoring import (
    DIMENSION_WEIGHTS,
    ScoringInput,
    compute_score,
    score_pricing,
    score_sentiment,
    threat_level_for,
)


def rich_input() -> ScoringInput:
    return ScoringInput(
        product_count=6,
        products_with_description=6,
        feature_count=22,
        features_per_product=5.0,
        pricing_plan_count=4,
        plans_with_public_amount=3,
        has_free_tier=True,
        has_custom_pricing=True,
        pricing_page_found=True,
        value_proposition_count=4,
        has_positioning_statement=True,
        target_audience_count=3,
        ai_confidence=0.8,
        pages_crawled=18,
        has_sitemap=True,
        has_robots_txt=True,
        has_blog=True,
        structured_data_type_count=3,
        avg_word_count=850,
        internal_link_count=120,
        marketing_channel_count=4,
        social_profile_count=4,
        case_study_pages=3,
        cta_count=5,
        open_graph_tag_count=6,
        has_favicon=True,
        external_link_count=30,
    )


class TestMissingData:
    def test_dimension_without_data_scores_none_not_zero(self) -> None:
        result = compute_score(ScoringInput(pages_crawled=4))
        assert result.dimensions["product"]["score"] is None
        assert result.dimensions["pricing"]["score"] is None

    def test_missing_dimensions_are_excluded_from_the_overall(self) -> None:
        """A competitor we know little about must not be scored as a weak one."""
        sparse = compute_score(
            ScoringInput(
                pages_crawled=2,
                product_count=3,
                products_with_description=3,
                features_per_product=4,
            )
        )
        assert sparse.overall is not None
        assert sparse.overall > 20  # would be near zero if nulls counted as zeros
        assert sparse.data_completeness < 0.5

    def test_sentiment_is_unavailable_without_review_data(self) -> None:
        dimension = score_sentiment(ScoringInput())
        assert dimension.score is None
        assert "review" in dimension.rationale.lower()

    def test_no_data_at_all_yields_no_overall_score(self) -> None:
        result = compute_score(ScoringInput())
        assert result.overall is None
        assert result.threat_level == "unknown"


class TestPrecisionAndReproducibility:
    def test_scores_are_rounded_to_steps_of_five(self) -> None:
        result = compute_score(rich_input())
        assert result.overall is not None
        assert result.overall % 5 == 0

    def test_scoring_is_deterministic(self) -> None:
        first = compute_score(rich_input())
        second = compute_score(rich_input())
        assert first.overall == second.overall
        assert first.dimensions == second.dimensions

    def test_confidence_is_reduced_for_a_shallow_crawl(self) -> None:
        deep = rich_input()
        shallow = rich_input()
        shallow.pages_crawled = 2
        assert compute_score(shallow).confidence < compute_score(deep).confidence


class TestRationale:
    def test_every_dimension_explains_itself(self) -> None:
        result = compute_score(rich_input())
        for name, dimension in result.dimensions.items():
            assert dimension["rationale"], f"{name} has no rationale"
            assert dimension["inputs"], f"{name} recorded no inputs"

    def test_seo_rationale_states_what_is_not_measured(self) -> None:
        result = compute_score(rich_input())
        assert "not measured" in result.dimensions["seo"]["rationale"]


class TestPricingDimension:
    def test_published_prices_beat_hidden_prices(self) -> None:
        transparent = score_pricing(
            ScoringInput(pricing_page_found=True, pricing_plan_count=3, plans_with_public_amount=3)
        )
        opaque = score_pricing(
            ScoringInput(
                pricing_page_found=True,
                pricing_plan_count=3,
                plans_with_public_amount=0,
                has_custom_pricing=True,
            )
        )
        assert transparent.score > opaque.score

    def test_no_pricing_information_is_unscored(self) -> None:
        assert score_pricing(ScoringInput()).score is None


class TestThreatLevel:
    def test_thresholds(self) -> None:
        assert threat_level_for(85, 1.0) == "critical"
        assert threat_level_for(65, 1.0) == "high"
        assert threat_level_for(45, 1.0) == "moderate"
        assert threat_level_for(20, 1.0) == "low"

    def test_withheld_when_data_is_too_incomplete(self) -> None:
        assert threat_level_for(90, 0.3) == "unknown"


def test_weights_sum_to_one() -> None:
    assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9
