"""What the development provider is allowed to call a pricing plan.

The competitor pricing table is the highest-trust surface in the product: a user reads it
and repriced. These tests pin down that it never shows a number it cannot justify calling
a plan.
"""

from __future__ import annotations

import pytest

from app.ai.prompts import extraction
from app.ai.providers.mock_provider import MockProvider
from app.db.models.enums import BillingPeriod

PAGES = [
    {
        "url": "https://example.com/",
        "page_type": "home",
        "title": "Example",
        "text": "Some page text.",
        "headings": ["Example"],
        "word_count": 200,
    }
]


async def extract(prices):
    provider = MockProvider()
    spec = extraction.build(company_name="Example", pages=PAGES, observed_prices=prices)
    result = await provider.complete_structured(spec, model="mock", max_tokens=1000)
    return result.data


def price(amount, *, currency="EUR", preceding="", page_type="pricing", period=None):
    return {
        "amount": amount,
        "currency": currency,
        "period": period,
        "context": f"{preceding} {currency} {amount}",
        "preceding": preceding,
        "page_type": page_type,
        "source_url": f"https://example.com/{page_type}",
    }


class TestNamedPlans:
    async def test_a_price_next_to_a_plan_name_becomes_a_plan(self) -> None:
        result = await extract(
            [
                price(0, preceding="Free", period="month"),
                price(59, preceding="Pro", period="month"),
            ]
        )

        assert [(plan.name, float(plan.amount)) for plan in result.pricing_plans] == [
            ("Free", 0.0),
            ("Pro", 59.0),
        ]
        assert all(plan.billing_period is BillingPeriod.MONTHLY for plan in result.pricing_plans)

    async def test_the_nearest_preceding_name_wins(self) -> None:
        """Every tier on a pricing page sits within a window of the next one, so taking
        the first match would label every price "Free"."""
        result = await extract(
            [
                price(0, preceding="Pricing Free"),
                price(59, preceding="Free EUR 0 per month Pro"),
            ]
        )
        assert [plan.name for plan in result.pricing_plans] == ["Free", "Pro"]


class TestUnnamedPrices:
    async def test_a_price_with_no_plan_name_is_not_invented_into_one(self) -> None:
        """A shop's product prices are prices, not plans. Calling them "Plan 5" dresses a
        raw observation up as structured intelligence, which is the failure this product
        exists to avoid."""
        result = await extract(
            [
                price(2.53, preceding="Fishing rod", page_type="home"),
                price(4.95, currency="BGN", preceding="Hooks", page_type="home"),
                price(22.0, currency="BGN", preceding="Landing net", page_type="home"),
            ]
        )

        assert result.pricing_plans == []
        assert result.pricing_model_notes is not None
        assert "3 price(s) were found" in result.pricing_model_notes

    async def test_the_gap_is_explained_rather_than_left_blank(self) -> None:
        result = await extract([])
        assert result.pricing_plans == []
        assert result.pricing_model_notes == "No prices were found on the crawled pages."

    async def test_named_plans_survive_alongside_unnamed_prices(self) -> None:
        result = await extract(
            [
                price(59, preceding="Pro", period="month"),
                price(7, preceding="and", period=None),
            ]
        )

        assert [plan.name for plan in result.pricing_plans] == ["Pro"]
        assert "1 further price(s)" in (result.pricing_model_notes or "")


@pytest.mark.parametrize("page_type", ["home", "about", "blog", "features"])
async def test_prices_away_from_a_pricing_page_are_never_plans(page_type: str) -> None:
    """A number on an about page or a changelog is incidental. Only a pricing page can
    carry a pricing tier."""
    result = await extract([price(1.0, currency="USD", preceding="Pro", page_type=page_type)])
    assert result.pricing_plans == []
