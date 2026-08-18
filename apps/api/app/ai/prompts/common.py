"""Shared prompt scaffolding.

Every prompt in this package is built from the same three parts:

1. a role line describing the analytical task,
2. the **house rules** below, which are identical for every prompt, and
3. the untrusted-content rule from :mod:`app.ai.sanitize`.

Keeping the rules in one place means "never invent a price" is enforced consistently
rather than being remembered in four out of five prompts.
"""

from __future__ import annotations

from app.ai.sanitize import untrusted_content_rule

# The single most important instruction in the product.  A competitor intelligence tool
# that invents facts is worse than no tool: the user acts on it.
HOUSE_RULES = """\
Evidence rules, which take precedence over every other consideration:

1. Base every statement only on the supplied content. Do not use outside knowledge about
   the company, even if you recognise it. If you recognise the brand, ignore what you
   know and analyse only what is provided.
2. Never invent a number. Prices, customer counts, funding, headcount, review scores and
   percentages may only be reported if they appear verbatim in the supplied content.
3. When the content does not support a field, leave it empty and list the topic in
   `insufficient_data_for` where that field exists. An empty answer is correct; a
   plausible guess is a defect.
4. Distinguish what the site claims from what is true. "The site claims 99.9% uptime" is
   an observation. "They have 99.9% uptime" is not.
5. Quote sparingly and briefly when supporting a claim, and only from the supplied
   content.
6. Write in plain, specific English. No marketing language of your own, no filler, no
   restating the question.
"""


def build_system_prompt(role: str, nonce: str, extra_rules: str = "") -> str:
    """Assemble a system prompt: role, house rules, untrusted-content rule."""
    sections = [role.strip(), HOUSE_RULES.strip(), untrusted_content_rule(nonce)]
    if extra_rules.strip():
        sections.insert(2, extra_rules.strip())
    return "\n\n".join(sections)


def format_observed_prices(prices: list[dict]) -> str:
    """Render the prices the crawler actually parsed.

    Supplied to the model as a closed list: the extraction prompt is told it may only
    report amounts drawn from it, and the service enforces the same rule afterwards.
    """
    if not prices:
        return "No prices were detected in the page text."
    lines = []
    for price in prices[:40]:
        period = f" per {price['period']}" if price.get("period") else ""
        lines.append(
            f"- {price.get('currency', '?')} {price.get('amount')}{period} "
            f"(seen near: \"{str(price.get('context', ''))[:160]}\")"
        )
    return "\n".join(lines)


def format_pages(pages: list[dict], *, max_chars_per_page: int) -> str:
    """Render crawled pages with their type and URL so the model can cite sources."""
    blocks = []
    for page in pages:
        text = str(page.get("text", ""))[:max_chars_per_page]
        blocks.append(
            f"### PAGE: {page.get('page_type', 'other')} — {page.get('url', '')}\n"
            f"TITLE: {page.get('title') or '(none)'}\n"
            f"{text}"
        )
    return "\n\n".join(blocks)


__all__ = ["HOUSE_RULES", "build_system_prompt", "format_observed_prices", "format_pages"]
