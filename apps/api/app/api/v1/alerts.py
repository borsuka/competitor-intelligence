"""Alert rules and in-app notifications."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import MemberScope, Scope, SessionDep
from app.schemas.common import Paginated, page_meta
from app.schemas.competitor import (
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleUpdate,
    MarkReadRequest,
    NotificationResponse,
)
from app.services import alerts as alert_service

router = APIRouter(prefix="/orgs/{organization_id}", tags=["alerts"])


@router.get("/alerts", response_model=list[AlertRuleResponse])
async def list_rules(session: SessionDep, scope: Scope) -> list[AlertRuleResponse]:
    rules = await alert_service.list_rules(session, scope)
    return [AlertRuleResponse.model_validate(rule) for rule in rules]


@router.post("/alerts", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: AlertRuleCreate, session: SessionDep, scope: MemberScope
) -> AlertRuleResponse:
    rule = await alert_service.create_rule(
        session,
        scope,
        name=payload.name,
        competitor_id=payload.competitor_id,
        change_types=[change_type.value for change_type in payload.change_types],
        min_severity=payload.min_severity,
        channels=[channel.value for channel in payload.channels],
        webhook_url=payload.webhook_url,
    )
    await session.commit()
    return AlertRuleResponse.model_validate(rule)


@router.patch("/alerts/{rule_id}", response_model=AlertRuleResponse)
async def update_rule(
    rule_id: uuid.UUID,
    payload: AlertRuleUpdate,
    session: SessionDep,
    scope: MemberScope,
) -> AlertRuleResponse:
    fields = payload.model_dump(exclude_unset=True)
    if "change_types" in fields and fields["change_types"] is not None:
        fields["change_types"] = [value.value for value in payload.change_types or []]
    if "channels" in fields and fields["channels"] is not None:
        fields["channels"] = [value.value for value in payload.channels or []]

    rule = await alert_service.update_rule(session, scope, rule_id, **fields)
    await session.commit()
    return AlertRuleResponse.model_validate(rule)


@router.delete("/alerts/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID, session: SessionDep, scope: MemberScope) -> None:
    await alert_service.delete_rule(session, scope, rule_id)
    await session.commit()


@router.get("/notifications", response_model=Paginated[NotificationResponse])
async def list_notifications(
    session: SessionDep,
    scope: Scope,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[NotificationResponse]:
    items, total = await alert_service.list_notifications(
        session, scope, unread_only=unread_only, limit=limit, offset=offset
    )
    return Paginated(
        items=[NotificationResponse.model_validate(item) for item in items],
        meta=page_meta(total=total, limit=limit, offset=offset, count=len(items)),
    )


@router.get("/notifications/unread-count")
async def unread_count(session: SessionDep, scope: Scope) -> dict[str, int]:
    return {"count": await alert_service.unread_count(session, scope)}


@router.post("/notifications/read")
async def mark_read(
    payload: MarkReadRequest, session: SessionDep, scope: MemberScope
) -> dict[str, int]:
    updated = await alert_service.mark_read(session, scope, payload.notification_ids)
    await session.commit()
    return {"updated": updated}


__all__ = ["router"]
