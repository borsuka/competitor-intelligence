"""Change detection.

The hard part of this feature is not finding differences — it is *not* reporting the
thousands of meaningless ones a website produces between two crawls.  A user who gets
"the homepage changed" every day stops reading alerts, and the feature is dead.

So the comparison happens on normalised, structured state rather than on markup:

* pricing is compared per plan, on amount and currency,
* products and features are compared as sets of normalised names,
* page text is compared on a hash of boilerplate-stripped content,
* positioning is compared as text, and only a substantial rewrite counts.

Severity comes from magnitude, so a 20% price rise outranks a new blog post.

The functions here are pure: they take two snapshots of state and return change records.
That makes the behaviour testable without a database and keeps persistence in
:mod:`app.services.analysis`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.db.models.enums import ChangeType, Severity

# Below this, a text diff is template noise (a copyright year, a rotating testimonial).
TEXT_CHANGE_MIN_RATIO = 0.15
# A price move smaller than this is a currency rounding artefact, not a pricing decision.
PRICE_CHANGE_MIN_RATIO = 0.01


def normalize_name(value: str) -> str:
    """Identity key for a product or plan across analyses."""
    cleaned = re.sub(r"[^\w\s]", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass(slots=True)
class PlanState:
    name: str
    amount: float | None
    currency: str | None
    billing_period: str = "unknown"
    is_custom_pricing: bool = False
    source_url: str | None = None

    @property
    def key(self) -> str:
        return f"{normalize_name(self.name)}|{self.billing_period}"


@dataclass(slots=True)
class CompetitorState:
    """The comparable state of one competitor at one point in time."""

    plans: list[PlanState] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    positioning: str | None = None
    page_hashes: dict[str, str] = field(default_factory=dict)  # url -> text_hash
    page_titles: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DetectedChange:
    change_type: ChangeType
    severity: Severity
    title: str
    description: str
    entity_key: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    magnitude: float | None = None
    source_url: str | None = None


def _format_price(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "custom pricing"
    prefix = f"{currency} " if currency else ""
    # Trim a pointless ".0" — "EUR 59" reads better than "EUR 59.0".
    rendered = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{prefix}{rendered}"


def _price_severity(ratio: float) -> Severity:
    if ratio >= 0.20:
        return Severity.HIGH
    if ratio >= 0.05:
        return Severity.MEDIUM
    return Severity.LOW


def _diff_pricing(
    previous: list[PlanState], current: list[PlanState], competitor: str
) -> list[DetectedChange]:
    changes: list[DetectedChange] = []
    old_by_key = {plan.key: plan for plan in previous}
    new_by_key = {plan.key: plan for plan in current}

    for key, new_plan in new_by_key.items():
        old_plan = old_by_key.get(key)
        if old_plan is None:
            changes.append(
                DetectedChange(
                    change_type=ChangeType.PLAN_ADDED,
                    severity=Severity.MEDIUM,
                    title=f"{competitor} added a new plan: {new_plan.name}",
                    description=(
                        f'A pricing tier named "{new_plan.name}" appeared, priced at '
                        f"{_format_price(new_plan.amount, new_plan.currency)}."
                    ),
                    entity_key=key,
                    after=_plan_dict(new_plan),
                    source_url=new_plan.source_url,
                )
            )
            continue

        if old_plan.amount is None and new_plan.amount is not None:
            changes.append(
                DetectedChange(
                    change_type=ChangeType.PRICE_DECREASED,
                    severity=Severity.MEDIUM,
                    title=f"{competitor} published a price for {new_plan.name}",
                    description=(
                        f'"{new_plan.name}" previously showed custom pricing and now lists '
                        f"{_format_price(new_plan.amount, new_plan.currency)}."
                    ),
                    entity_key=key,
                    before=_plan_dict(old_plan),
                    after=_plan_dict(new_plan),
                    source_url=new_plan.source_url,
                )
            )
            continue

        if old_plan.amount is not None and new_plan.amount is None:
            changes.append(
                DetectedChange(
                    change_type=ChangeType.PRICE_INCREASED,
                    severity=Severity.MEDIUM,
                    title=f"{competitor} removed the published price for {new_plan.name}",
                    description=(
                        f'"{new_plan.name}" previously listed '
                        f"{_format_price(old_plan.amount, old_plan.currency)} and now shows "
                        "custom pricing."
                    ),
                    entity_key=key,
                    before=_plan_dict(old_plan),
                    after=_plan_dict(new_plan),
                    source_url=new_plan.source_url,
                )
            )
            continue

        if old_plan.amount is None or new_plan.amount is None:
            continue
        if old_plan.amount == 0:
            continue

        delta = new_plan.amount - old_plan.amount
        ratio = abs(delta) / old_plan.amount
        if ratio < PRICE_CHANGE_MIN_RATIO:
            continue

        increased = delta > 0
        changes.append(
            DetectedChange(
                change_type=(
                    ChangeType.PRICE_INCREASED if increased else ChangeType.PRICE_DECREASED
                ),
                severity=_price_severity(ratio),
                title=(
                    f"{competitor} {'increased' if increased else 'reduced'} "
                    f"{new_plan.name} from {_format_price(old_plan.amount, old_plan.currency)} "
                    f"to {_format_price(new_plan.amount, new_plan.currency)}"
                ),
                description=(
                    f"The {new_plan.name} plan moved from "
                    f"{_format_price(old_plan.amount, old_plan.currency)} to "
                    f"{_format_price(new_plan.amount, new_plan.currency)} "
                    f"({'+' if increased else '-'}{ratio * 100:.1f}%)."
                ),
                entity_key=key,
                before=_plan_dict(old_plan),
                after=_plan_dict(new_plan),
                magnitude=round(ratio, 4),
                source_url=new_plan.source_url,
            )
        )

    for key, old_plan in old_by_key.items():
        if key in new_by_key:
            continue
        changes.append(
            DetectedChange(
                change_type=ChangeType.PLAN_REMOVED,
                severity=Severity.MEDIUM,
                title=f"{competitor} removed the {old_plan.name} plan",
                description=(
                    f'The pricing tier "{old_plan.name}" '
                    f"({_format_price(old_plan.amount, old_plan.currency)}) is no longer listed."
                ),
                entity_key=key,
                before=_plan_dict(old_plan),
                source_url=old_plan.source_url,
            )
        )

    return changes


def _plan_dict(plan: PlanState) -> dict[str, Any]:
    return {
        "name": plan.name,
        "amount": plan.amount,
        "currency": plan.currency,
        "billing_period": plan.billing_period,
        "is_custom_pricing": plan.is_custom_pricing,
    }


def _diff_set(
    previous: list[str],
    current: list[str],
    *,
    competitor: str,
    added_type: ChangeType,
    removed_type: ChangeType,
    noun: str,
    severity: Severity,
    max_reported: int = 10,
) -> list[DetectedChange]:
    """Set difference over normalised names, with a cap on how much is reported.

    The cap matters: a site restructure can add sixty features at once, and sixty
    notifications is the same as none.
    """
    old_map = {normalize_name(item): item for item in previous if item}
    new_map = {normalize_name(item): item for item in current if item}

    added = [new_map[key] for key in new_map.keys() - old_map.keys()]
    removed = [old_map[key] for key in old_map.keys() - new_map.keys()]

    changes: list[DetectedChange] = []
    for item in sorted(added)[:max_reported]:
        changes.append(
            DetectedChange(
                change_type=added_type,
                severity=severity,
                title=f"{competitor} added {noun}: {item}",
                description=f'"{item}" now appears on the site and did not previously.',
                entity_key=normalize_name(item),
                after={"name": item},
            )
        )
    for item in sorted(removed)[:max_reported]:
        changes.append(
            DetectedChange(
                change_type=removed_type,
                severity=Severity.LOW,
                title=f"{competitor} removed {noun}: {item}",
                description=f'"{item}" is no longer present on the site.',
                entity_key=normalize_name(item),
                before={"name": item},
            )
        )
    return changes


def _text_similarity(left: str, right: str) -> float:
    """Jaccard similarity over word sets.

    Cheap and adequate: we need "is this substantially rewritten", not a fine-grained
    edit distance, and a token-set measure is unaffected by paragraph reordering.
    """
    left_words = set(left.lower().split())
    right_words = set(right.lower().split())
    if not left_words and not right_words:
        return 1.0
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def _diff_pages(
    previous: CompetitorState, current: CompetitorState, competitor: str
) -> list[DetectedChange]:
    changes: list[DetectedChange] = []

    added_pages = current.page_hashes.keys() - previous.page_hashes.keys()
    removed_pages = previous.page_hashes.keys() - current.page_hashes.keys()

    for url in sorted(added_pages)[:8]:
        title = current.page_titles.get(url) or url
        changes.append(
            DetectedChange(
                change_type=ChangeType.PAGE_ADDED,
                severity=Severity.LOW,
                title=f"{competitor} published a new page: {title}",
                description=f"A page not seen in the previous crawl is now live at {url}.",
                entity_key=url,
                after={"url": url, "title": title},
                source_url=url,
            )
        )

    for url in sorted(removed_pages)[:8]:
        changes.append(
            DetectedChange(
                change_type=ChangeType.PAGE_REMOVED,
                severity=Severity.LOW,
                title=f"{competitor} removed a page",
                description=f"{url} was present in the previous crawl and is no longer reachable.",
                entity_key=url,
                before={"url": url, "title": previous.page_titles.get(url)},
                source_url=url,
            )
        )

    changed = [
        url
        for url, digest in current.page_hashes.items()
        if url in previous.page_hashes and previous.page_hashes[url] != digest
    ]
    if changed:
        # Rolled up into one change: eleven separate "page changed" rows would drown
        # the pricing change that actually matters.
        sample = ", ".join(sorted(changed)[:3])
        changes.append(
            DetectedChange(
                change_type=ChangeType.CONTENT_CHANGED,
                severity=Severity.LOW if len(changed) < 3 else Severity.MEDIUM,
                title=f"{competitor} updated {len(changed)} page(s)",
                description=f"Content changed on: {sample}"
                + (f" and {len(changed) - 3} more." if len(changed) > 3 else "."),
                entity_key="content",
                after={"changed_pages": sorted(changed)[:20]},
                magnitude=float(len(changed)),
            )
        )

    return changes


def detect_changes(
    previous: CompetitorState, current: CompetitorState, *, competitor_name: str
) -> list[DetectedChange]:
    """Compare two states and return only the differences worth telling a user about."""
    changes: list[DetectedChange] = []

    changes.extend(_diff_pricing(previous.plans, current.plans, competitor_name))
    changes.extend(
        _diff_set(
            previous.products,
            current.products,
            competitor=competitor_name,
            added_type=ChangeType.PRODUCT_ADDED,
            removed_type=ChangeType.PRODUCT_REMOVED,
            noun="a product",
            severity=Severity.HIGH,
        )
    )
    changes.extend(
        _diff_set(
            previous.features,
            current.features,
            competitor=competitor_name,
            added_type=ChangeType.FEATURE_ADDED,
            removed_type=ChangeType.FEATURE_REMOVED,
            noun="a feature",
            severity=Severity.MEDIUM,
        )
    )

    if previous.positioning and current.positioning:
        similarity = _text_similarity(previous.positioning, current.positioning)
        if similarity < (1.0 - TEXT_CHANGE_MIN_RATIO):
            changes.append(
                DetectedChange(
                    change_type=ChangeType.POSITIONING_CHANGED,
                    severity=Severity.HIGH if similarity < 0.5 else Severity.MEDIUM,
                    title=f"{competitor_name} changed how it positions itself",
                    description=(
                        "The positioning described on the site was substantially rewritten "
                        f"({(1 - similarity) * 100:.0f}% of the wording differs)."
                    ),
                    entity_key="positioning",
                    before={"positioning": previous.positioning[:1000]},
                    after={"positioning": current.positioning[:1000]},
                    magnitude=round(1 - similarity, 3),
                )
            )

    changes.extend(_diff_pages(previous, current, competitor_name))

    # Highest severity first, so a truncated list keeps the important half.
    changes.sort(key=lambda change: -change.severity.rank)
    return changes


__all__ = [
    "PRICE_CHANGE_MIN_RATIO",
    "TEXT_CHANGE_MIN_RATIO",
    "CompetitorState",
    "DetectedChange",
    "PlanState",
    "detect_changes",
    "normalize_name",
]
