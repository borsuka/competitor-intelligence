"""The AI provider boundary.

Everything above this line (services, workers, API) knows only :class:`AIProvider` and
:class:`AIResult`.  Swapping providers, or adding a second one, therefore touches this
package and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

TSchema = TypeVar("TSchema", bound=BaseModel)


@dataclass
class AIResult(Generic[TSchema]):
    """A validated provider response plus the metadata needed for cost and provenance."""

    data: TSchema
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    is_mock: bool = False
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    tokens_in: int = 0
    # True when the vectors come from the local lexical hasher rather than a trained
    # embedding model.  Surfaced through the search API so results are never presented as
    # semantic when they are only lexical.
    is_lexical_fallback: bool = False


@dataclass(slots=True)
class PromptSpec:
    """A prompt ready to send: system text, user text, and the schema of the answer."""

    system: str
    user: str
    schema: type[BaseModel]
    version: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AIProvider(Protocol):
    """Structured completion.

    The only method the application uses.  There is deliberately no "give me text"
    escape hatch: every call site must declare the schema it expects, which is what makes
    unvalidated model output impossible to consume by accident.
    """

    name: str
    is_mock: bool

    async def complete_structured(
        self,
        spec: PromptSpec,
        *,
        model: str,
        max_tokens: int,
    ) -> AIResult[Any]: ...

    async def aclose(self) -> None: ...


class EmbeddingProvider(Protocol):
    name: str
    dimensions: int

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...

    async def aclose(self) -> None: ...


__all__ = [
    "AIProvider",
    "EmbeddingProvider",
    "AIResult",
    "EmbeddingResult",
    "PromptSpec",
    "TSchema",
]
