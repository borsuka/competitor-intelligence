"""Alert rules, matching and notification delivery.

Notifications are persisted *before* delivery is attempted, so a failed send is visible in
the product rather than lost in a log.  Channels sit behind :class:`NotificationChannel`
implementations: in-app works today, email and webhook are wired but require configuration.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.tenancy import Role, TenantScope
from app.db.base import utcnow
from app.db.models.competitor import Competitor
from app.db.models.enums import (
    ChangeType,
    NotificationStatus,
    Severity,
)
from app.db.models.enums import (
    NotificationChannel as Channel,
)
from app.db.models.monitoring import AlertRule, Change, Notification
from app.scraping.urls import assert_safe_url
from app.services import audit

log = get_logger(__name__)


class NotificationSender(Protocol):
    channel: Channel

    async def send(self, notification: Notification) -> None: ...


class InAppSender:
    """The default channel: the notification row *is* the delivery."""

    channel = Channel.IN_APP

    async def send(self, notification: Notification) -> None:
        notification.status = NotificationStatus.SENT
        notification.sent_at = utcnow()


class ConsoleEmailSender:
    """Development email channel.

    Logs the message instead of sending it.  Explicitly not a silent no-op: the
    notification is marked ``sent`` only because it genuinely reached its configured
    destination, which in development is the log.  Configure SMTP to deliver for real.
    """

    channel = Channel.EMAIL

    async def send(self, notification: Notification) -> None:
        settings = get_settings()
        if settings.smtp_host:
            raise NotImplementedError(
                "SMTP delivery is configured but not implemented; "
                "wire an SMTP client into ConsoleEmailSender before enabling it."
            )
        log.info(
            "notification.email.console",
            title=notification.title,
            organization_id=str(notification.organization_id),
        )
        notification.status = NotificationStatus.SENT
        notification.sent_at = utcnow()


class WebhookSender:
    """POSTs the change to a user-supplied URL.

    The URL comes from a user, so it goes through the same SSRF guard as a competitor
    website — an alert webhook pointed at ``169.254.169.254`` is the same attack with a
    different entry point.
    """

    channel = Channel.WEBHOOK

    def __init__(self, url: str) -> None:
        self._url = url

    async def send(self, notification: Notification) -> None:
        assert_safe_url(self._url)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(
                    self._url,
                    json={
                        "title": notification.title,
                        "body": notification.body,
                        "payload": notification.payload,
                    },
                    headers={"User-Agent": get_settings().scraper_user_agent},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            notification.status = NotificationStatus.FAILED
            notification.error_message = str(exc)[:500]
            raise
        notification.status = NotificationStatus.SENT
        notification.sent_at = utcnow()


# ------------------------------------------------------------------- rules


async def list_rules(session: AsyncSession, scope: TenantScope) -> list[AlertRule]:
    result = await session.execute(
        select(AlertRule)
        .where(AlertRule.organization_id == scope.organization_id)
        .order_by(AlertRule.created_at.desc())
    )
    return list(result.scalars().all())


async def get_rule(session: AsyncSession, scope: TenantScope, rule_id: uuid.UUID) -> AlertRule:
    result = await session.execute(
        select(AlertRule).where(
            AlertRule.id == rule_id, AlertRule.organization_id == scope.organization_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise NotFoundError("Alert rule not found.", code="alert_rule_not_found")
    return rule


def _validate_rule_input(
    change_types: list[str] | None, channels: list[str] | None, webhook_url: str | None
) -> tuple[list[str], list[str]]:
    valid_types = {change_type.value for change_type in ChangeType}
    selected_types = [value for value in (change_types or []) if value in valid_types]

    valid_channels = {channel.value for channel in Channel}
    selected_channels = [value for value in (channels or []) if value in valid_channels]
    if not selected_channels:
        selected_channels = [Channel.IN_APP.value]

    if Channel.WEBHOOK.value in selected_channels:
        if not webhook_url:
            raise ValidationError(
                "A webhook URL is required for the webhook channel.", code="webhook_url_required"
            )
        assert_safe_url(webhook_url)

    return selected_types, selected_channels


async def create_rule(
    session: AsyncSession,
    scope: TenantScope,
    *,
    name: str,
    competitor_id: uuid.UUID | None = None,
    change_types: list[str] | None = None,
    min_severity: Severity = Severity.MEDIUM,
    channels: list[str] | None = None,
    webhook_url: str | None = None,
) -> AlertRule:
    scope.require(Role.MEMBER)
    selected_types, selected_channels = _validate_rule_input(change_types, channels, webhook_url)

    if competitor_id is not None:
        exists = await session.execute(
            select(Competitor.id).where(
                Competitor.id == competitor_id,
                Competitor.organization_id == scope.organization_id,
                Competitor.deleted_at.is_(None),
            )
        )
        if exists.scalar_one_or_none() is None:
            raise NotFoundError("Competitor not found.", code="competitor_not_found")

    rule = AlertRule(
        organization_id=scope.organization_id,
        created_by_user_id=scope.user_id,
        competitor_id=competitor_id,
        name=name.strip()[:120] or "Alert",
        change_types=selected_types,
        min_severity=min_severity,
        channels=selected_channels,
        webhook_url=webhook_url,
    )
    session.add(rule)
    await session.flush()

    await audit.record(
        session,
        action="alert_rule.created",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="alert_rule",
        resource_id=rule.id,
    )
    return rule


async def update_rule(
    session: AsyncSession, scope: TenantScope, rule_id: uuid.UUID, **fields
) -> AlertRule:
    scope.require(Role.MEMBER)
    rule = await get_rule(session, scope, rule_id)

    if (name := fields.get("name")) is not None:
        rule.name = name.strip()[:120] or rule.name
    if (is_active := fields.get("is_active")) is not None:
        rule.is_active = is_active
    if (severity := fields.get("min_severity")) is not None:
        rule.min_severity = severity
    if "webhook_url" in fields:
        rule.webhook_url = fields["webhook_url"]
    if "change_types" in fields or "channels" in fields:
        types, channels = _validate_rule_input(
            fields.get("change_types", rule.change_types),
            fields.get("channels", rule.channels),
            rule.webhook_url,
        )
        rule.change_types = types
        rule.channels = channels
    return rule


async def delete_rule(session: AsyncSession, scope: TenantScope, rule_id: uuid.UUID) -> None:
    scope.require(Role.MEMBER)
    rule = await get_rule(session, scope, rule_id)
    await session.delete(rule)


def rule_matches(rule: AlertRule, change: Change) -> bool:
    """An empty ``change_types`` means "any type" — that is the useful default."""
    if not rule.is_active:
        return False
    if rule.competitor_id is not None and rule.competitor_id != change.competitor_id:
        return False
    if rule.change_types and change.change_type.value not in rule.change_types:
        return False
    return change.severity.rank >= rule.min_severity.rank


# --------------------------------------------------------------- dispatching


async def dispatch_for_changes(
    session: AsyncSession, *, competitor: Competitor, changes: list[Change]
) -> list[Notification]:
    """Create and attempt delivery of notifications for newly detected changes."""
    result = await session.execute(
        select(AlertRule).where(
            AlertRule.organization_id == competitor.organization_id,
            AlertRule.is_active.is_(True),
        )
    )
    rules = list(result.scalars().all())
    if not rules:
        return []

    created: list[Notification] = []
    now = utcnow()

    for change in changes:
        for rule in rules:
            if not rule_matches(rule, change):
                continue
            for channel_value in rule.channels:
                channel = Channel(channel_value)
                notification = Notification(
                    created_at=now,
                    organization_id=competitor.organization_id,
                    alert_rule_id=rule.id,
                    change_id=change.id,
                    channel=channel,
                    status=NotificationStatus.PENDING,
                    title=change.title[:300],
                    body=change.description,
                    payload={
                        "competitor_id": str(competitor.id),
                        "competitor_name": competitor.name,
                        "change_type": change.change_type.value,
                        "severity": change.severity.value,
                        "before": change.before,
                        "after": change.after,
                        "source_url": change.source_url,
                    },
                )
                session.add(notification)
                created.append(notification)

    await session.flush()

    for notification in created:
        rule = next((r for r in rules if r.id == notification.alert_rule_id), None)
        sender = _sender_for(notification.channel, rule)
        notification.attempts += 1
        try:
            await sender.send(notification)
        except Exception as exc:
            notification.status = NotificationStatus.FAILED
            notification.error_message = str(exc)[:500]
            log.warning(
                "notification.delivery_failed",
                channel=notification.channel.value,
                organization_id=str(notification.organization_id),
                error=str(exc)[:200],
            )

    return created


def _sender_for(channel: Channel, rule: AlertRule | None) -> NotificationSender:
    if channel is Channel.EMAIL:
        return ConsoleEmailSender()
    if channel is Channel.WEBHOOK and rule is not None and rule.webhook_url:
        return WebhookSender(rule.webhook_url)
    return InAppSender()


# ------------------------------------------------------------ notifications


async def list_notifications(
    session: AsyncSession,
    scope: TenantScope,
    *,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    query = select(Notification).where(
        Notification.organization_id == scope.organization_id,
        Notification.channel == Channel.IN_APP,
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))

    total = await session.execute(select(func.count()).select_from(query.subquery()))
    result = await session.execute(
        query.order_by(Notification.created_at.desc()).limit(min(limit, 100)).offset(offset)
    )
    return list(result.scalars().all()), int(total.scalar_one() or 0)


async def unread_count(session: AsyncSession, scope: TenantScope) -> int:
    result = await session.execute(
        select(func.count(Notification.id)).where(
            Notification.organization_id == scope.organization_id,
            Notification.channel == Channel.IN_APP,
            Notification.read_at.is_(None),
        )
    )
    return int(result.scalar_one() or 0)


async def mark_read(
    session: AsyncSession, scope: TenantScope, notification_ids: list[uuid.UUID] | None = None
) -> int:
    statement = (
        update(Notification)
        .where(
            Notification.organization_id == scope.organization_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow(), status=NotificationStatus.READ)
    )
    if notification_ids:
        statement = statement.where(Notification.id.in_(notification_ids))
    result = await session.execute(statement)
    return int(result.rowcount or 0)


async def acknowledge_change(
    session: AsyncSession, scope: TenantScope, change_id: uuid.UUID
) -> Change:
    result = await session.execute(
        select(Change).where(
            Change.id == change_id, Change.organization_id == scope.organization_id
        )
    )
    change = result.scalar_one_or_none()
    if change is None:
        raise NotFoundError("Change not found.", code="change_not_found")
    change.acknowledged_at = utcnow()
    return change


__all__ = [
    "ConsoleEmailSender",
    "InAppSender",
    "WebhookSender",
    "acknowledge_change",
    "create_rule",
    "delete_rule",
    "dispatch_for_changes",
    "get_rule",
    "list_notifications",
    "list_rules",
    "mark_read",
    "rule_matches",
    "unread_count",
    "update_rule",
]
