"""Stage 1 — extract what the company sells and what it charges.

Runs on the cheap model: this is a reading-comprehension task over supplied text, not a
reasoning task, and paying synthesis-model rates for it would triple the cost of an
analysis for no gain.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import PromptSpec
from app.ai.prompts.common import build_system_prompt, format_observed_prices, format_pages
from app.ai.sanitize import fence, make_nonce
from app.ai.schemas import ExtractionResult

VERSION = "extraction/1.0.0"
NAME = "extraction"

ROLE = """\
You extract product and pricing facts from a company's public website.

You are given the text of several pages that were crawled from one company's site, plus
the list of prices that were parsed from that text by a deterministic parser.

Produce a structured record of:
- the products or services the company sells,
- its pricing tiers,
- the product capabilities the site emphasises.\
"""

PRICING_RULES = """\
Pricing rules:

- The `observed prices` list below is the complete set of amounts a parser found in the
  page text. You may only use amounts from that list. If a plan's price is not in the
  list, set `amount` to null.
- A plan whose page says "Contact us", "Talk to sales" or similar is `is_custom_pricing:
  true` with a null amount. This is a correct answer, not a missing one.
- A plan advertised at no cost is `is_free: true` with `amount: 0`.
- Set `billing_period` only when the page states it. Otherwise leave it `unknown`.
- Set `source_url` to the page the plan was described on.\
"""


def build(
    *,
    company_name: str,
    pages: list[dict[str, Any]],
    observed_prices: list[dict[str, Any]],
    max_chars_per_page: int = 6000,
) -> PromptSpec:
    nonce = make_nonce()

    content = format_pages(pages, max_chars_per_page=max_chars_per_page)
    user = (
        f"Company: {company_name}\n\n"
        f"Observed prices (the only amounts you may report):\n"
        f"{format_observed_prices(observed_prices)}\n\n"
        f"Crawled page content:\n"
        f"{fence(content, nonce=nonce)}\n\n"
        "Extract the products, pricing tiers and key features described above."
    )

    return PromptSpec(
        system=build_system_prompt(ROLE, nonce, PRICING_RULES),
        user=user,
        schema=ExtractionResult,
        version=VERSION,
        name=NAME,
        metadata={
            "company_name": company_name,
            "observed_prices": observed_prices,
            "headings": [h for page in pages for h in page.get("headings", [])],
            "primary_url": pages[0].get("url") if pages else None,
        },
    )


__all__ = ["NAME", "VERSION", "build"]
