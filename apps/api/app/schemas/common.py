"""Shared response building blocks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """Base for every response model.

    ``from_attributes`` lets a router return an ORM object directly; the schema decides
    what is exposed, so adding a column to a table never silently widens the API.
    """

    model_config = ConfigDict(from_attributes=True)


class PageMeta(APIModel):
    total: int
    limit: int
    offset: int
    has_more: bool


class Paginated[T](APIModel):
    items: list[T]
    meta: PageMeta


class ErrorBody(APIModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(APIModel):
    """The single error shape every failing endpoint returns."""

    error: ErrorBody


class MessageResponse(APIModel):
    message: str


class HealthResponse(APIModel):
    status: str
    version: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)


class TimestampedModel(APIModel):
    created_at: datetime
    updated_at: datetime


def page_meta(*, total: int, limit: int, offset: int, count: int) -> PageMeta:
    return PageMeta(total=total, limit=limit, offset=offset, has_more=offset + count < total)


__all__ = [
    "APIModel",
    "ErrorBody",
    "ErrorResponse",
    "HealthResponse",
    "MessageResponse",
    "PageMeta",
    "Paginated",
    "TimestampedModel",
    "page_meta",
]
