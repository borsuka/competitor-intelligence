"""Embedding providers.

Two implementations, and the difference between them is stated everywhere the results
are used:

* :class:`VoyageEmbedder` — a real embedding model, used when ``VOYAGE_API_KEY`` is set.
  Produces semantic vectors: "how do I cancel" retrieves the refund policy.
* :class:`LexicalHashingEmbedder` — the offline default.  A hashed bag-of-words
  vectoriser: a genuine, deterministic retrieval method, but a *lexical* one.  It matches
  shared vocabulary, not shared meaning.  Results carry ``is_lexical_fallback=True`` and
  the search API passes that flag to the UI, so lexical results are never presented as
  semantic ones.

The fallback exists so that vector search works out of the box rather than being an
empty feature behind a missing key.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

import httpx

from app.ai.base import EmbeddingResult
from app.core.config import get_settings
from app.core.errors import AIProviderError
from app.core.logging import get_logger

log = get_logger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# Common words carry no retrieval signal and would dominate a bag-of-words vector.
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the to was
    were will with you your we our us this these those they them their he she his her""".split()
)


def _tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN_PATTERN.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    ]


class LexicalHashingEmbedder:
    """Feature-hashing vectoriser. Offline, deterministic, lexical-only."""

    name = "lexical-hash"
    model = "lexical-hash-v1"

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions or get_settings().embedding_dimensions

    def _vector(self, text: str) -> list[float]:
        counts = Counter(_tokenize(text))
        vector = [0.0] * self.dimensions
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            # Signed hashing keeps unrelated collisions from always reinforcing.
            sign = 1.0 if digest[4] & 1 else -1.0
            # Sub-linear term frequency: a word repeated 50 times is not 50x as important.
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[self._vector(text) for text in texts],
            provider=self.name,
            model=self.model,
            dimensions=self.dimensions,
            tokens_in=sum(len(text) // 4 for text in texts),
            is_lexical_fallback=True,
        )

    async def aclose(self) -> None:
        return None


class VoyageEmbedder:
    """Real embeddings via the Voyage AI API."""

    name = "voyage"
    ENDPOINT = "https://api.voyageai.com/v1/embeddings"
    MAX_BATCH = 96

    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        self._api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors: list[list[float]] = []
        tokens_in = 0

        for start in range(0, len(texts), self.MAX_BATCH):
            batch = texts[start : start + self.MAX_BATCH]
            try:
                response = await self._client.post(
                    self.ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"input": batch, "model": self.model, "input_type": "document"},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AIProviderError(
                    "The embedding provider request failed.", code="embedding_provider_error"
                ) from exc

            payload = response.json()
            for item in sorted(payload.get("data", []), key=lambda d: d.get("index", 0)):
                vectors.append(item["embedding"])
            tokens_in += int(payload.get("usage", {}).get("total_tokens", 0))

        if vectors and len(vectors[0]) != self.dimensions:
            # A dimension mismatch would silently corrupt the vector column.
            raise AIProviderError(
                f"Embedding model returned {len(vectors[0])} dimensions, "
                f"but the schema expects {self.dimensions}.",
                code="embedding_dimension_mismatch",
            )

        return EmbeddingResult(
            vectors=vectors,
            provider=self.name,
            model=self.model,
            dimensions=self.dimensions,
            tokens_in=tokens_in,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_embedder() -> LexicalHashingEmbedder | VoyageEmbedder:
    settings = get_settings()
    if settings.voyage_api_key:
        return VoyageEmbedder(
            api_key=settings.voyage_api_key.get_secret_value(),
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    log.info("ai.embedder.lexical_fallback", reason="VOYAGE_API_KEY not configured")
    return LexicalHashingEmbedder(settings.embedding_dimensions)


def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    """Split text on paragraph boundaries, with a small overlap.

    Overlap matters: a sentence split across two chunks is retrievable from neither
    without it.
    """
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    chunks: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    current = ""

    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= max_chars:
            current = f"{current}\n{paragraph}" if current else paragraph
            continue
        if current:
            chunks.append(current)
            current = current[-overlap:] + "\n" + paragraph if overlap else paragraph
        else:
            # A single paragraph longer than the budget: hard-split it.
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start : start + max_chars])
            current = ""
        if len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = ""

    if current.strip():
        chunks.append(current)
    return chunks


__all__ = ["LexicalHashingEmbedder", "VoyageEmbedder", "build_embedder", "chunk_text"]
