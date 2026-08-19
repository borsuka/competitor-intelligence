"""Stage 2 — positioning, audience, strengths and weaknesses.

Runs on the stronger model and, importantly, runs on the *extracted facts* plus a trimmed
slice of page text — not on raw HTML.  Feeding a synthesis model raw markup wastes most
of the context window on navigation and CSS class names.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import PromptSpec
from app.ai.prompts.common import build_system_prompt, format_pages
from app.ai.sanitize import fence, make_nonce
from app.ai.schemas import PositioningResult

VERSION = "positioning/1.0.0"
NAME = "positioning"

ROLE = """\
You are a competitive analyst. You read a company's public website and describe how that
company positions itself in its market.

Cover:
- what the company does, in two or three sentences a business reader would understand,
- who it appears to sell to,
- the value propositions it leads with,
- its positioning relative to the market (premium, low-cost, specialist, all-in-one, ...),
- the marketing channels visible from the site,
- its apparent strengths and weaknesses as a competitor.\
"""

ANALYSIS_RULES = """\
Strengths and weaknesses:

- A strength or weakness must be about the company as a competitor, and must be grounded
  in something visible on the site. "No pricing is published" is a valid weakness. "Their
  support is slow" is not, unless the site says so.
- Include the supporting evidence: a short quote and the page URL it came from.
- Three well-grounded points beat eight speculative ones. If you can only support two,
  return two.
- `confidence` reflects how much the supplied content supported this analysis: 0.8+ only
  when several substantive pages were available, below 0.4 when the crawl returned little.
- `marketing_channels` may list only channels evidenced by the site — a linked social
  profile, a visible blog, a newsletter form, a case-study section, a careers page listing
  a growth team. Do not speculate about paid advertising you cannot see.\
"""


def build(
    *,
    company_name: str,
    domain: str,
    pages: list[dict[str, Any]],
    extracted_summary: str,
    external_links: list[str],
    max_chars_per_page: int = 5000,
) -> PromptSpec:
    nonce = make_nonce()

    content = format_pages(pages, max_chars_per_page=max_chars_per_page)
    social = "\n".join(f"- {link}" for link in external_links[:30]) or "None found."

    user = (
        f"Company: {company_name} ({domain})\n\n"
        f"Already extracted from this site:\n{extracted_summary}\n\n"
        f"Outbound links found on the site (useful for identifying marketing channels):\n"
        f"{social}\n\n"
        f"Crawled page content:\n{fence(content, nonce=nonce)}\n\n"
        "Analyse how this company positions itself, and where it is strong and weak "
        "as a competitor."
    )

    return PromptSpec(
        system=build_system_prompt(ROLE, nonce, ANALYSIS_RULES),
        user=user,
        schema=PositioningResult,
        version=VERSION,
        name=NAME,
        metadata={
            "company_name": company_name,
            "domain": domain,
            "page_title": pages[0].get("title") if pages else None,
            "meta_description": pages[0].get("meta_description") if pages else None,
            "calls_to_action": [cta for page in pages for cta in page.get("calls_to_action", [])][
                :10
            ],
            "word_count": sum(int(page.get("word_count") or 0) for page in pages),
            "marketing_channels": [],
        },
    )


__all__ = ["NAME", "VERSION", "build"]
