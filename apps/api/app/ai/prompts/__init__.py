"""Prompt modules.

One module per analysis stage.  Each exports ``VERSION``, ``NAME`` and ``build(...)``,
which returns a :class:`app.ai.base.PromptSpec`.

``VERSION`` is stored on every analysis row.  When a prompt changes, historical analyses
still record which wording produced them; without that, a change in output quality is
impossible to attribute.
"""

from app.ai.prompts import (
    change_summary,
    common,
    comparison,
    extraction,
    positioning,
    recommendations,
)

PROMPT_VERSIONS: dict[str, str] = {
    extraction.NAME: extraction.VERSION,
    positioning.NAME: positioning.VERSION,
    recommendations.NAME: recommendations.VERSION,
    comparison.NAME: comparison.VERSION,
    change_summary.NAME: change_summary.VERSION,
}

__all__ = [
    "extraction",
    "positioning",
    "recommendations",
    "comparison",
    "change_summary",
    "common",
    "PROMPT_VERSIONS",
]
