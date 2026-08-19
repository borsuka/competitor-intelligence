"""FastAPI dependencies: session, current user, tenant scope, request metadata.

The dependency chain is the authorization model.  A router that declares
``scope: TenantScope = Depends(require_member)`` cannot be reached without a valid
session *and* a membership in the organization named in the path — there is no way to
forget the check, because the argument the handler needs is produced by it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, NotFoundError
from app.core.security import decode_token
from app.core.tenancy import Role, TenantScope
from app.db.models.identity import User
from app.db.session import get_session
from app.services import organizations

ACCESS_COOKIE = "sentinel_access"
REFRESH_COOKIE = "sentinel_refresh"
CSRF_COOKIE = "sentinel_csrf"
CSRF_HEADER = "X-CSRF-Token"

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    """Best-effort client address.

    ``X-Forwarded-For`` is only trusted because the deployment terminates TLS at a proxy
    that overwrites it. Behind an untrusted proxy this header is attacker-controlled and
    the value is used for logging and rate limiting only, never for authorization.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent", "")[:255] or None


async def get_current_user(
    session: SessionDep,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> User:
    """Resolve the signed-in user from the access cookie."""
    if not access_token:
        raise AuthenticationError()

    payload = decode_token(access_token, "access")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError() from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_member(
    session: SessionDep,
    user: CurrentUser,
    organization_id: Annotated[uuid.UUID, Path()],
) -> TenantScope:
    """Produce the tenant scope for this request, or 404.

    A non-member gets 404 rather than 403: a 403 would confirm that the organization
    exists, which is enough to enumerate ids.
    """
    membership = await organizations.get_membership(
        session, organization_id=organization_id, user_id=user.id
    )
    if membership is None:
        raise NotFoundError("Organization not found.", code="organization_not_found")
    return TenantScope(organization_id=organization_id, user_id=user.id, role=Role(membership.role))


Scope = Annotated[TenantScope, Depends(require_member)]


def require_role(minimum: Role):
    """Dependency factory for endpoints that need more than membership."""

    async def _dependency(scope: Scope) -> TenantScope:
        scope.require(minimum)
        return scope

    return _dependency


AdminScope = Annotated[TenantScope, Depends(require_role(Role.ADMIN))]
MemberScope = Annotated[TenantScope, Depends(require_role(Role.MEMBER))]


__all__ = [
    "ACCESS_COOKIE",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "REFRESH_COOKIE",
    "AdminScope",
    "CurrentUser",
    "MemberScope",
    "Scope",
    "SessionDep",
    "client_ip",
    "get_current_user",
    "require_member",
    "require_role",
    "user_agent",
]
