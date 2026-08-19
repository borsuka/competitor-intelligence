"""Customer review data.

**No review source is configured in this deployment.** This module exists so that
connecting one is a matter of writing a provider and setting a config value, rather than
threading review data through the scoring engine and the AI layer after the fact.

The default :class:`NullReviewProvider` returns nothing, and every consumer treats "no
reviews" as *insufficient data* — the sentiment score is `null`, not zero, and the AI
sentiment prompt is skipped rather than run on an empty list. Fabricating reviews to fill
the gap would be the single most damaging thing this product could do.

To connect a real source (G2, Capterra, Trustpilot, an app store), implement
:class:`ReviewProvider` and return it from :func:`build_review_provider`. Nothing in
`scoring.py` or `analysis.py` changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Review:
    """One review, as published by its source.

    ``source`` and ``source_url`` are required rather than optional: an insight derived
    from reviews must be traceable back to where the review was published, or a user
    cannot check it.
    """

    source: str
    source_url: str
    rating: float | None
    max_rating: float | None
    title: str | None
    body: str
    author: str | None
    published_at: datetime | None

    @property
    def normalized_rating(self) -> float | None:
        """Rating on a 0-1 scale, so sources with different scales are comparable."""
        if self.rating is None or not self.max_rating:
            return None
        return max(0.0, min(1.0, self.rating / self.max_rating))


@dataclass(slots=True)
class ReviewBatch:
    """What a provider returned for one competitor."""

    reviews: list[Review] = field(default_factory=list)
    provider: str = "none"
    # True when no source is configured, as opposed to a source that returned nothing.
    # The UI says "connect a review source" in the first case and "no reviews found" in
    # the second, which are different messages to a user.
    is_unconfigured: bool = True

    @property
    def count(self) -> int:
        return len(self.reviews)

    @property
    def average_rating(self) -> float | None:
        """Mean normalised rating, or ``None`` when no review carried one."""
        ratings = [
            review.normalized_rating
            for review in self.reviews
            if review.normalized_rating is not None
        ]
        if not ratings:
            return None
        return sum(ratings) / len(ratings)

    @property
    def sentiment_score(self) -> float | None:
        """Ratings mapped onto -1..1, which is what the scoring engine expects.

        Derived from ratings, not from the review text: a star rating is the author's own
        summary of their experience, and inferring sentiment from prose when a rating is
        right there would be inventing precision.
        """
        average = self.average_rating
        if average is None:
            return None
        return (average * 2.0) - 1.0


class ReviewProvider(Protocol):
    """A source of publicly published reviews for a competitor."""

    name: str

    async def fetch(self, *, domain: str, company_name: str, limit: int = 100) -> ReviewBatch:
        """Return published reviews, or an empty batch when the source has none."""
        ...

    async def aclose(self) -> None: ...


class NullReviewProvider:
    """The default. Reports honestly that no source is configured."""

    name = "none"

    async def fetch(self, *, domain: str, company_name: str, limit: int = 100) -> ReviewBatch:
        _ = (domain, company_name, limit)
        return ReviewBatch(reviews=[], provider=self.name, is_unconfigured=True)

    async def aclose(self) -> None:
        return None


def build_review_provider() -> ReviewProvider:
    """Select the configured provider.

    Only the null provider exists today. A real one belongs here, selected by a config
    value, alongside whatever credentials it needs.
    """
    return NullReviewProvider()


__all__ = [
    "NullReviewProvider",
    "Review",
    "ReviewBatch",
    "ReviewProvider",
    "build_review_provider",
]
