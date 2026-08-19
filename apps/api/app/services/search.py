"""Vector search over stored competitor content.

Uses pgvector's cosine distance operator through SQLAlchemy's typed comparator, so the
query is parameterised — the embedding is never interpolated into SQL.

The response always carries which embedding provider produced the index.  With the
offline hashing embedder the results are lexical, not semantic, and the UI says so rather
than letting a user assume the search understands meaning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import AIService
from app.core.errors import ValidationError
from app.core.tenancy import TenantScope
from app.db.models.analysis import Embedding
from app.db.models.competitor import Competitor

MAX_QUERY_LENGTH = 500


@dataclass(slots=True)
class SearchHit:
    competitor_id: uuid.UUID
    competitor_name: str
    content: str
    source_url: str | None
    similarity: float


@dataclass(slots=True)
class SearchResponse:
    hits: list[SearchHit]
    provider: str
    is_lexical: bool
    query: str


async def semantic_search(
    session: AsyncSession,
    scope: TenantScope,
    *,
    query: str,
    competitor_id: uuid.UUID | None = None,
    limit: int = 10,
    ai: AIService | None = None,
) -> SearchResponse:
    cleaned = query.strip()
    if not cleaned:
        raise ValidationError("Enter something to search for.", code="query_required")
    if len(cleaned) > MAX_QUERY_LENGTH:
        cleaned = cleaned[:MAX_QUERY_LENGTH]

    ai = ai or AIService()
    embedded = await ai.embed([cleaned])
    if not embedded.vectors:
        return SearchResponse(hits=[], provider=embedded.provider, is_lexical=True, query=cleaned)

    vector = embedded.vectors[0]
    distance = Embedding.embedding.cosine_distance(vector)

    statement = (
        select(Embedding, Competitor.name, distance.label("distance"))
        .join(Competitor, Competitor.id == Embedding.competitor_id)
        .where(
            Embedding.organization_id == scope.organization_id,
            Competitor.deleted_at.is_(None),
        )
        .order_by(distance)
        .limit(min(limit, 50))
    )
    if competitor_id is not None:
        statement = statement.where(Embedding.competitor_id == competitor_id)

    result = await session.execute(statement)

    hits = [
        SearchHit(
            competitor_id=embedding.competitor_id,
            competitor_name=name,
            content=embedding.content[:600],
            source_url=embedding.source_url,
            similarity=round(1.0 - float(dist), 4),
        )
        for embedding, name, dist in result.all()
    ]

    return SearchResponse(
        hits=hits,
        provider=embedded.provider,
        is_lexical=embedded.is_lexical_fallback,
        query=cleaned,
    )


__all__ = ["SearchHit", "SearchResponse", "semantic_search"]
