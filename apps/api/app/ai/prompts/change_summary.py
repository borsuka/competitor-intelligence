"""Turn a computed diff into a sentence a human wants to read.

The diff itself is produced by :mod:`app.services.changes` — deterministically, from
stored data.  The model only phrases it, and is given the before/after values explicitly
so it cannot get the numbers wrong.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import PromptSpec
from app.ai.prompts.common import build_system_prompt
from app.ai.sanitize import fence, make_nonce
from app.ai.schemas import ChangeSummary

VERSION = "change_summary/1.0.0"
NAME = "change_summary"

ROLE = """\
You write one-line notifications about changes detected on a competitor's website.

You are given a change that a deterministic detector already computed, including its exact
before and after values. Write the headline a busy product manager should see.\
"""

STYLE_RULES = """\
Style rules:

- The headline is one sentence, under 140 characters, and states the concrete change:
  "Acme raised its Pro plan from EUR 49 to EUR 59 per month."
- Use the exact values supplied. Never round, restate or reinterpret them.
- `why_it_matters` is one short sentence, and only if there is something real to say.
  Leave it empty rather than writing filler.
- No exclamation marks, no urgency language, no "breaking".\
"""


def build(*, competitor_name: str, change: dict[str, Any]) -> PromptSpec:
    nonce = make_nonce()

    user = (
        f"Competitor: {competitor_name}\n"
        f"Change type: {change.get('change_type')}\n"
        f"Severity: {change.get('severity')}\n"
        f"Entity: {change.get('entity_key') or '(none)'}\n"
        f"Before: {change.get('before')}\n"
        f"After: {change.get('after')}\n"
        f"Source page: {change.get('source_url') or '(unknown)'}\n\n"
        f"Detector's own description:\n"
        f"{fence(str(change.get('description') or ''), nonce=nonce, label='detector output')}\n\n"
        "Write the notification."
    )

    return PromptSpec(
        system=build_system_prompt(ROLE, nonce, STYLE_RULES),
        user=user,
        schema=ChangeSummary,
        version=VERSION,
        name=NAME,
        metadata={
            "headline": change.get("title"),
            "explanation": change.get("description"),
        },
    )


__all__ = ["NAME", "VERSION", "build"]
