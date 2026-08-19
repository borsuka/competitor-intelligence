"""Stage 3 — what the user's own company should consider doing.

This prompt is only useful when the organization has described its own company, so the
service skips it entirely when that context is missing.  Generic advice ("consider
improving your SEO") is noise, and shipping noise trains users to ignore the panel.
"""

from __future__ import annotations

from app.ai.base import PromptSpec
from app.ai.prompts.common import build_system_prompt
from app.ai.sanitize import fence, make_nonce
from app.ai.schemas import RecommendationResult

VERSION = "recommendations/1.0.0"
NAME = "recommendations"

ROLE = """\
You advise a company on how to respond to a specific competitor.

You are given a description of the user's own company and a completed analysis of one
competitor. Produce concrete, prioritised actions the user's company could take in
response.\
"""

ADVICE_RULES = """\
Recommendation rules:

- Each recommendation must reference something specific in the competitor analysis. "The
  competitor publishes per-seat pricing while you do not" is specific. "Improve your
  marketing" is not.
- Recommend at most five actions. Fewer, sharper recommendations are more useful than a
  long list.
- `priority` reflects competitive urgency; `effort` reflects implementation cost. Do not
  mark everything high priority.
- Never recommend anything that depends on facts you were not given.
- If the competitor analysis is too thin to support any grounded recommendation, return
  an empty list.\
"""


def build(
    *,
    own_company_name: str,
    own_company_description: str,
    competitor_name: str,
    analysis_summary: str,
) -> PromptSpec:
    nonce = make_nonce()
    # The analysis is itself derived from the competitor's own website, so it stays
    # inside the fence: content that was untrusted upstream does not become trusted by
    # having passed through a model.
    analysis_block = fence(
        analysis_summary,
        nonce=nonce,
        label="competitor analysis derived from their website",
    )

    user = (
        f"The user's company: {own_company_name}\n"
        f"{own_company_description}\n\n"
        f"Competitor analysed: {competitor_name}\n"
        f"Analysis of that competitor:\n{analysis_block}\n\n"
        f"What should {own_company_name} consider doing in response to {competitor_name}?"
    )

    return PromptSpec(
        system=build_system_prompt(ROLE, nonce, ADVICE_RULES),
        user=user,
        schema=RecommendationResult,
        version=VERSION,
        name=NAME,
        metadata={"competitor_name": competitor_name, "include_placeholder": True},
    )


__all__ = ["NAME", "VERSION", "build"]
