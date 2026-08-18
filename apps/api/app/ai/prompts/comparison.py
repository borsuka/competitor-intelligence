"""Cross-competitor comparison narrative.

The score matrix itself is computed deterministically by
:mod:`app.services.scoring` and passed in.  The model's job is to explain the matrix and
identify openings — not to produce the numbers, which would make them unreproducible.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import PromptSpec
from app.ai.prompts.common import build_system_prompt
from app.ai.sanitize import fence, make_nonce
from app.ai.schemas import ComparisonInsights

VERSION = "comparison/1.0.0"
NAME = "comparison"

ROLE = """\
You compare several competitors against each other and explain what the comparison means.

You are given, for each competitor: a summary derived from their website, their extracted
products and pricing, and a score matrix that was computed deterministically from observed
data.\
"""

COMPARISON_RULES = """\
Comparison rules:

- The scores were computed by a deterministic engine from observed data. Explain them;
  do not recompute, dispute or invent them.
- Name competitors exactly as they are named in the input.
- "Biggest opportunity" means a gap none of these competitors currently covers well, and
  it must be visible in the supplied data.
- A dimension marked "insufficient data" is not a weakness. Do not treat a missing score
  as a low score.
- If the competitors are too similar or the data too thin for a meaningful comparison,
  say that plainly in `summary` and leave the other fields empty.\
"""


def build(*, competitors: list[dict[str, Any]], matrix: dict[str, Any]) -> PromptSpec:
    nonce = make_nonce()

    blocks = []
    for competitor in competitors:
        blocks.append(
            f"### {competitor['name']} ({competitor.get('domain', '')})\n"
            f"Summary: {competitor.get('summary') or '(no analysis available)'}\n"
            f"Products: {', '.join(competitor.get('products', [])) or 'none extracted'}\n"
            f"Pricing: {competitor.get('pricing_text') or 'none extracted'}\n"
            f"Strengths: {'; '.join(competitor.get('strengths', [])) or 'none recorded'}\n"
            f"Weaknesses: {'; '.join(competitor.get('weaknesses', [])) or 'none recorded'}"
        )

    matrix_lines = []
    for dimension, values in matrix.get("dimensions", {}).items():
        rendered = ", ".join(
            f"{name}: {'insufficient data' if score is None else round(score)}"
            for name, score in values.items()
        )
        matrix_lines.append(f"- {dimension}: {rendered}")

    user = (
        "Score matrix (computed deterministically from observed data):\n"
        + ("\n".join(matrix_lines) or "(no scores available)")
        + "\n\nCompetitor profiles:\n"
        + fence("\n\n".join(blocks), nonce=nonce, label="competitor analyses derived from their websites")
        + "\n\nCompare these competitors and identify the strongest, the biggest threat, "
        "and where the openings are."
    )

    return PromptSpec(
        system=build_system_prompt(ROLE, nonce, COMPARISON_RULES),
        user=user,
        schema=ComparisonInsights,
        version=VERSION,
        name=NAME,
        metadata={
            "competitor_names": [c["name"] for c in competitors],
            "overall_scores": {
                c["name"]: c.get("overall_score") for c in competitors
            },
        },
    )


__all__ = ["build", "VERSION", "NAME"]
