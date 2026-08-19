"""Tenant scoping.

Every organization-owned query in the service layer takes a :class:`TenantScope`.  The
point is not convenience — it is that "which organization is this query for?" becomes an
argument the caller cannot omit, instead of an ambient value someone forgets to apply.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from uuid import UUID


class Role(enum.StrEnum):
    """Organization roles, ordered from most to least privileged."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def can(self, required: Role) -> bool:
        """True when this role is at least as privileged as ``required``."""
        return self.rank <= required.rank


_ROLE_RANK: dict[Role, int] = {
    Role.OWNER: 0,
    Role.ADMIN: 1,
    Role.MEMBER: 2,
    Role.VIEWER: 3,
}


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The organization a request is acting within, plus who is acting."""

    organization_id: UUID
    user_id: UUID
    role: Role

    def require(self, minimum: Role) -> None:
        from app.core.errors import PermissionDeniedError

        if not self.role.can(minimum):
            raise PermissionDeniedError(f"This action requires the {minimum.value} role or higher.")

    @property
    def is_read_only(self) -> bool:
        return self.role is Role.VIEWER


__all__ = ["Role", "TenantScope"]
