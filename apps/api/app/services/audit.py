"""Audit logging.

The only place that writes :class:`AuditLog`.  Rows are append-only: nothing in the
codebase updates or deletes them, because an editable audit trail is not an audit trail.

Payloads are filtered before they are stored — the audit log is read by support staff,
and it must not become a second place where secrets accumulate.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models.identity import AuditLog

log = get_logger(__name__)

# Mirrors the logging redaction list; kept explicit rather than imported so a change to
# log formatting cannot silently widen what gets persisted.
_FORBIDDEN_KEYS = ("password", "token", "secret", "key", "cookie", "authorization")


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if not any(part in key.lower() for part in _FORBIDDEN_KEYS)
    }


async def record(
    session: AsyncSession,
    *,
    action: str,
    organization_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry to the current transaction.

    Deliberately does not commit: the audit row lands atomically with the action it
    describes, so a rolled-back operation leaves no misleading trace.
    """
    entry = AuditLog(
        created_at=utcnow(),
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
        metadata_=_safe_metadata(metadata),
    )
    session.add(entry)
    log.info(
        "audit",
        action=action,
        organization_id=str(organization_id) if organization_id else None,
        actor_user_id=str(actor_user_id) if actor_user_id else None,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
    )


__all__ = ["record"]
