"""Anthropic provider.

JSON is obtained through a single forced tool call rather than by asking for JSON in
prose.  The schema is the tool's ``input_schema``, so the model is constrained by the
API itself and there is no fenced-code-block parsing to get wrong.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.ai.base import AIResult, PromptSpec
from app.core.config import get_settings
from app.core.errors import AIProviderError, AIResponseInvalidError
from app.core.logging import get_logger

log = get_logger(__name__)

TOOL_NAME = "record_analysis"


class _Transient(Exception):
    """Marker for failures worth retrying (rate limits, 5xx, connection resets)."""


class AnthropicProvider:
    name = "anthropic"
    is_mock = False

    def __init__(self, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        settings = get_settings()
        key = api_key or (
            settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        )
        if not key:
            raise AIProviderError(
                "AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.",
                code="ai_not_configured",
            )
        self._client = AsyncAnthropic(api_key=key, timeout=settings.ai_timeout_seconds)

    async def complete_structured(
        self,
        spec: PromptSpec,
        *,
        model: str,
        max_tokens: int,
    ) -> AIResult[Any]:
        started = time.perf_counter()
        payload, usage = await self._call(spec, model=model, max_tokens=max_tokens)

        try:
            data = spec.schema.model_validate(payload)
        except PydanticValidationError as exc:
            # Not retried: a schema mismatch is a prompt bug, and retrying burns money to
            # get the same answer.  The error carries the field errors for the logs.
            log.warning(
                "ai.response_invalid",
                prompt=spec.name,
                version=spec.version,
                errors=exc.errors()[:5],
            )
            raise AIResponseInvalidError(
                "The AI response did not match the expected structure.",
                details={"prompt": spec.name, "error_count": len(exc.errors())},
            ) from exc

        return AIResult(
            data=data,
            provider=self.name,
            model=model,
            tokens_in=usage[0],
            tokens_out=usage[1],
            is_mock=False,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    @retry(
        retry=retry_if_exception_type(_Transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=2, max=30),
        reraise=True,
    )
    async def _call(
        self, spec: PromptSpec, *, model: str, max_tokens: int
    ) -> tuple[dict[str, Any], tuple[int, int]]:
        import anthropic

        schema = spec.schema.model_json_schema()
        try:
            message = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=spec.system,
                messages=[{"role": "user", "content": spec.user}],
                tools=[
                    {
                        "name": TOOL_NAME,
                        "description": (
                            "Record the structured result of the analysis. "
                            "Every field must be supported by the supplied content."
                        ),
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise _Transient(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise _Transient(str(exc)) from exc
            raise AIProviderError(
                "The AI provider rejected the request.",
                details={"status": exc.status_code},
            ) from exc
        except anthropic.AnthropicError as exc:
            raise AIProviderError("The AI provider request failed.") from exc

        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                usage = (message.usage.input_tokens, message.usage.output_tokens)
                return dict(block.input), usage

        raise AIResponseInvalidError(
            "The AI provider returned no structured result.",
            details={"prompt": spec.name, "stop_reason": message.stop_reason},
        )

    async def aclose(self) -> None:
        await self._client.close()


__all__ = ["AnthropicProvider", "TOOL_NAME"]
