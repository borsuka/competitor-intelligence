"""Change detection.

The value of this feature is what it *does not* report, so most of these tests assert
silence.
"""

from __future__ import annotations

from app.db.models.enums import ChangeType, Severity
from app.services.changes import (
    CompetitorState,
    PlanState,
    detect_changes,
    normalize_name,
)


def state(**kwargs) -> CompetitorState:
    return CompetitorState(**kwargs)


class TestPricing:
    def test_price_increase_is_detected_with_magnitude(self) -> None:
        before = state(plans=[PlanState("Pro", 49.0, "EUR", "monthly")])
        after = state(plans=[PlanState("Pro", 59.0, "EUR", "monthly")])

        changes = detect_changes(before, after, competitor_name="Acme")

        assert len(changes) == 1
        change = changes[0]
        assert change.change_type is ChangeType.PRICE_INCREASED
        assert change.severity is Severity.HIGH  # >20% move
        assert change.before["amount"] == 49.0
        assert change.after["amount"] == 59.0
        assert "EUR 49" in change.title and "EUR 59" in change.title

    def test_small_price_move_is_ignored_as_rounding(self) -> None:
        before = state(plans=[PlanState("Pro", 100.0, "EUR", "monthly")])
        after = state(plans=[PlanState("Pro", 100.5, "EUR", "monthly")])
        assert detect_changes(before, after, competitor_name="Acme") == []

    def test_severity_scales_with_magnitude(self) -> None:
        def severity_for(new_price: float) -> Severity:
            changes = detect_changes(
                state(plans=[PlanState("Pro", 100.0, "EUR", "monthly")]),
                state(plans=[PlanState("Pro", new_price, "EUR", "monthly")]),
                competitor_name="Acme",
            )
            return changes[0].severity

        assert severity_for(103.0) is Severity.LOW
        assert severity_for(110.0) is Severity.MEDIUM
        assert severity_for(130.0) is Severity.HIGH

    def test_plan_added_and_removed(self) -> None:
        before = state(plans=[PlanState("Pro", 49.0, "EUR", "monthly")])
        after = state(
            plans=[PlanState("Enterprise", None, None, "monthly", is_custom_pricing=True)]
        )

        types = {c.change_type for c in detect_changes(before, after, competitor_name="Acme")}
        assert types == {ChangeType.PLAN_ADDED, ChangeType.PLAN_REMOVED}

    def test_plan_name_casing_is_not_a_change(self) -> None:
        before = state(plans=[PlanState("Pro Plan", 49.0, "EUR", "monthly")])
        after = state(plans=[PlanState("pro plan.", 49.0, "EUR", "monthly")])
        assert detect_changes(before, after, competitor_name="Acme") == []


class TestProductsAndFeatures:
    def test_new_product_is_high_severity(self) -> None:
        changes = detect_changes(
            state(products=["Analytics"]),
            state(products=["Analytics", "AI Assistant"]),
            competitor_name="Acme",
        )
        assert len(changes) == 1
        assert changes[0].change_type is ChangeType.PRODUCT_ADDED
        assert changes[0].severity is Severity.HIGH

    def test_feature_churn_is_capped(self) -> None:
        """A site restructure must not produce sixty notifications."""
        changes = detect_changes(
            state(features=[]),
            state(features=[f"Feature {i}" for i in range(60)]),
            competitor_name="Acme",
        )
        assert len(changes) == 10

    def test_reordering_is_not_a_change(self) -> None:
        before = state(features=["A", "B", "C"])
        after = state(features=["C", "A", "B"])
        assert detect_changes(before, after, competitor_name="Acme") == []


class TestPositioning:
    def test_substantial_rewrite_is_reported(self) -> None:
        changes = detect_changes(
            state(positioning="The simplest invoicing tool for freelancers."),
            state(positioning="Enterprise revenue infrastructure for global finance teams."),
            competitor_name="Acme",
        )
        assert changes[0].change_type is ChangeType.POSITIONING_CHANGED
        assert changes[0].severity is Severity.HIGH

    def test_minor_edit_is_ignored(self) -> None:
        before = state(positioning="The simplest invoicing tool for growing freelancers today")
        after = state(positioning="The simplest invoicing tool for growing freelancers")
        assert detect_changes(before, after, competitor_name="Acme") == []


class TestPages:
    def test_changed_pages_are_rolled_up_into_one_change(self) -> None:
        before = state(page_hashes={f"https://acme.com/{i}": "old" for i in range(5)})
        after = state(page_hashes={f"https://acme.com/{i}": "new" for i in range(5)})

        changes = detect_changes(before, after, competitor_name="Acme")

        assert len(changes) == 1
        assert changes[0].change_type is ChangeType.CONTENT_CHANGED
        assert changes[0].magnitude == 5.0

    def test_identical_hashes_produce_nothing(self) -> None:
        pages = {"https://acme.com/": "same"}
        assert (
            detect_changes(
                state(page_hashes=pages), state(page_hashes=pages), competitor_name="Acme"
            )
            == []
        )

    def test_new_page_is_reported(self) -> None:
        changes = detect_changes(
            state(page_hashes={"https://acme.com/": "a"}),
            state(
                page_hashes={"https://acme.com/": "a", "https://acme.com/ai": "b"},
                page_titles={"https://acme.com/ai": "Introducing Acme AI"},
            ),
            competitor_name="Acme",
        )
        assert changes[0].change_type is ChangeType.PAGE_ADDED
        assert "Introducing Acme AI" in changes[0].title


def test_no_change_between_identical_states() -> None:
    current = state(
        plans=[PlanState("Pro", 49.0, "EUR", "monthly")],
        products=["Analytics"],
        features=["Dashboards"],
        positioning="A tool for teams.",
        page_hashes={"https://acme.com/": "abc"},
    )
    assert detect_changes(current, current, competitor_name="Acme") == []


def test_changes_are_ordered_by_severity() -> None:
    changes = detect_changes(
        state(
            plans=[PlanState("Pro", 100.0, "EUR", "monthly")], page_hashes={"https://a.com/x": "1"}
        ),
        state(
            plans=[PlanState("Pro", 150.0, "EUR", "monthly")],
            products=["New Thing"],
            page_hashes={"https://a.com/x": "2"},
        ),
        competitor_name="Acme",
    )
    ranks = [change.severity.rank for change in changes]
    assert ranks == sorted(ranks, reverse=True)


def test_normalize_name_collapses_punctuation_and_case() -> None:
    assert normalize_name("Pro  Plan!") == normalize_name("pro plan")
