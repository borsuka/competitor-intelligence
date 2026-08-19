"""Organizations, membership, invitations and quota accounting."""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, QuotaExceededError, ValidationError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.core.tenancy import Role, TenantScope
from app.db.base import utcnow
from app.db.models.competitor import Competitor
from app.db.models.enums import CompetitorStatus, OrgPlan
from app.db.models.identity import Invitation, Membership, Organization, UsageCounter, User
from app.services import audit

# Plan limits.  A dict rather than a table because these are product decisions that ship
# with the code; a per-organization override lives on the row when that day comes.
PLAN_LIMITS: dict[OrgPlan, dict[str, int]] = {
    OrgPlan.FREE: {"competitors": 3, "analyses_per_month": 20, "pages_per_month": 500},
    OrgPlan.STARTER: {"competitors": 10, "analyses_per_month": 100, "pages_per_month": 2_500},
    OrgPlan.GROWTH: {"competitors": 40, "analyses_per_month": 500, "pages_per_month": 12_000},
    OrgPlan.ENTERPRISE: {
        "competitors": 500,
        "analyses_per_month": 5_000,
        "pages_per_month": 150_000,
    },
}


@dataclass(slots=True)
class UsageSummary:
    period: str
    analyses_used: int
    analyses_limit: int
    pages_crawled: int
    pages_limit: int
    competitors_used: int
    competitors_limit: int
    ai_tokens_in: int
    ai_tokens_out: int


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "workspace")[:48]


def current_period() -> str:
    """Billing period key, ``YYYY-MM`` in UTC."""
    return utcnow().strftime("%Y-%m")


def limits_for(plan: OrgPlan) -> dict[str, int]:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[OrgPlan.FREE])


async def create_organization(
    session: AsyncSession, *, name: str, owner: User, plan: OrgPlan = OrgPlan.FREE
) -> Organization:
    """Create an organization with its creator as owner."""
    clean_name = name.strip()[:120]
    if not clean_name:
        raise ValidationError("The organization needs a name.", code="name_required")

    base_slug = slugify(clean_name)
    slug = base_slug
    # Slug collisions are expected ("Acme" twice), so suffix rather than reject.
    for _ in range(5):
        exists = await session.execute(select(Organization.id).where(Organization.slug == slug))
        if exists.scalar_one_or_none() is None:
            break
        slug = f"{base_slug}-{secrets.token_hex(3)}"
    else:
        raise ConflictError("Could not allocate a unique workspace address.")

    organization = Organization(name=clean_name, slug=slug, plan=plan)
    session.add(organization)
    await session.flush()

    session.add(Membership(organization_id=organization.id, user_id=owner.id, role=Role.OWNER))
    await session.flush()
    return organization


async def get_membership(
    session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    result = await session.execute(
        select(Membership)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
            Organization.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Organization, Role]]:
    result = await session.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user_id, Organization.deleted_at.is_(None))
        .order_by(Organization.created_at)
    )
    return [(org, role) for org, role in result.all()]


async def update_organization(session: AsyncSession, scope: TenantScope, **fields) -> Organization:
    scope.require(Role.ADMIN)
    organization = await session.get(Organization, scope.organization_id)
    if organization is None or organization.deleted_at is not None:
        raise NotFoundError("Organization not found.")

    allowed = {
        "name",
        "own_company_name",
        "own_company_url",
        "own_company_description",
    }
    for key, value in fields.items():
        if key in allowed and value is not None:
            setattr(organization, key, value)

    await audit.record(
        session,
        action="organization.updated",
        organization_id=organization.id,
        actor_user_id=scope.user_id,
        resource_type="organization",
        resource_id=organization.id,
        metadata={"fields": sorted(k for k in fields if k in allowed)},
    )
    return organization


# ------------------------------------------------------------------- members


async def list_members(session: AsyncSession, scope: TenantScope) -> list[tuple[Membership, User]]:
    result = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == scope.organization_id)
        .order_by(Membership.created_at)
    )
    return [(membership, user) for membership, user in result.all()]


async def invite_member(
    session: AsyncSession, scope: TenantScope, *, email: str, role: Role
) -> tuple[Invitation, str]:
    scope.require(Role.ADMIN)
    if role is Role.OWNER:
        raise ValidationError("Ownership is transferred, not invited.", code="cannot_invite_owner")

    normalized = email.strip().lower()

    existing = await session.execute(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == scope.organization_id, User.email == normalized)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("That person is already a member.", code="already_member")

    raw_token = generate_opaque_token(32)
    invitation = Invitation(
        organization_id=scope.organization_id,
        invited_by_user_id=scope.user_id,
        email=normalized,
        role=role,
        token_hash=hash_opaque_token(raw_token),
        expires_at=utcnow() + timedelta(days=7),
    )
    session.add(invitation)

    await audit.record(
        session,
        action="organization.member_invited",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="invitation",
        metadata={"role": role.value},
    )
    return invitation, raw_token


async def accept_invitation(session: AsyncSession, *, token: str, user: User) -> Membership:
    result = await session.execute(
        select(Invitation).where(Invitation.token_hash == hash_opaque_token(token))
    )
    invitation = result.scalar_one_or_none()
    now = utcnow()

    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise NotFoundError("That invitation is no longer valid.", code="invitation_invalid")

    # The invite is addressed to an email, so it cannot be redeemed by another account.
    if invitation.email != user.email:
        raise NotFoundError("That invitation is no longer valid.", code="invitation_invalid")

    existing = await get_membership(
        session, organization_id=invitation.organization_id, user_id=user.id
    )
    if existing is not None:
        invitation.accepted_at = now
        return existing

    membership = Membership(
        organization_id=invitation.organization_id, user_id=user.id, role=invitation.role
    )
    session.add(membership)
    invitation.accepted_at = now

    await audit.record(
        session,
        action="organization.member_joined",
        organization_id=invitation.organization_id,
        actor_user_id=user.id,
        resource_type="membership",
    )
    return membership


async def change_member_role(
    session: AsyncSession, scope: TenantScope, *, member_id: uuid.UUID, role: Role
) -> Membership:
    scope.require(Role.ADMIN)
    membership = await session.get(Membership, member_id)
    if membership is None or membership.organization_id != scope.organization_id:
        raise NotFoundError("Member not found.")

    if membership.role is Role.OWNER and role is not Role.OWNER:
        await _guard_last_owner(session, scope.organization_id)

    membership.role = role
    await audit.record(
        session,
        action="organization.member_role_changed",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="membership",
        resource_id=membership.id,
        metadata={"role": role.value},
    )
    return membership


async def remove_member(session: AsyncSession, scope: TenantScope, *, member_id: uuid.UUID) -> None:
    scope.require(Role.ADMIN)
    membership = await session.get(Membership, member_id)
    if membership is None or membership.organization_id != scope.organization_id:
        raise NotFoundError("Member not found.")
    if membership.role is Role.OWNER:
        await _guard_last_owner(session, scope.organization_id)

    await session.delete(membership)
    await audit.record(
        session,
        action="organization.member_removed",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="membership",
        resource_id=member_id,
    )


async def _guard_last_owner(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """An organization with no owner cannot be administered or deleted."""
    result = await session.execute(
        select(func.count(Membership.id)).where(
            Membership.organization_id == organization_id, Membership.role == Role.OWNER
        )
    )
    if (result.scalar_one() or 0) <= 1:
        raise ConflictError("This is the last owner of the organization.", code="last_owner")


# --------------------------------------------------------------------- quotas


async def _get_or_create_counter(session: AsyncSession, organization_id: uuid.UUID) -> UsageCounter:
    period = current_period()
    result = await session.execute(
        select(UsageCounter).where(
            UsageCounter.organization_id == organization_id, UsageCounter.period == period
        )
    )
    counter = result.scalar_one_or_none()
    if counter is None:
        counter = UsageCounter(organization_id=organization_id, period=period)
        session.add(counter)
        await session.flush()
    return counter


async def get_usage(session: AsyncSession, organization_id: uuid.UUID) -> UsageSummary:
    organization = await session.get(Organization, organization_id)
    plan = organization.plan if organization else OrgPlan.FREE
    limits = limits_for(plan)
    counter = await _get_or_create_counter(session, organization_id)

    competitor_count = await session.execute(
        select(func.count(Competitor.id)).where(
            Competitor.organization_id == organization_id,
            Competitor.deleted_at.is_(None),
            Competitor.status == CompetitorStatus.ACTIVE,
        )
    )

    return UsageSummary(
        period=counter.period,
        analyses_used=counter.analyses_used,
        analyses_limit=limits["analyses_per_month"],
        pages_crawled=counter.pages_crawled,
        pages_limit=limits["pages_per_month"],
        competitors_used=int(competitor_count.scalar_one() or 0),
        competitors_limit=limits["competitors"],
        ai_tokens_in=counter.ai_tokens_in,
        ai_tokens_out=counter.ai_tokens_out,
    )


async def assert_can_add_competitor(session: AsyncSession, organization_id: uuid.UUID) -> None:
    usage = await get_usage(session, organization_id)
    if usage.competitors_used >= usage.competitors_limit:
        raise QuotaExceededError(
            f"Your plan allows {usage.competitors_limit} tracked competitors. "
            "Archive one or upgrade to add more.",
            details={"limit": usage.competitors_limit, "used": usage.competitors_used},
        )


async def assert_can_run_analysis(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Checked before a job is enqueued, not after — a queued job has already cost money.

    Enforcing the limit at enqueue time is also what stops a scripted client from
    spending an organization's entire monthly AI budget in one burst.
    """
    usage = await get_usage(session, organization_id)
    if usage.analyses_used >= usage.analyses_limit:
        raise QuotaExceededError(
            f"Your plan includes {usage.analyses_limit} analyses per month. "
            "The counter resets at the start of next month.",
            details={"limit": usage.analyses_limit, "used": usage.analyses_used},
        )
    if usage.pages_crawled >= usage.pages_limit:
        raise QuotaExceededError(
            "Your organization has reached its monthly crawl limit.",
            details={"limit": usage.pages_limit, "used": usage.pages_crawled},
        )


async def record_usage(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    analyses: int = 0,
    pages: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    counter = await _get_or_create_counter(session, organization_id)
    counter.analyses_used += analyses
    counter.pages_crawled += pages
    counter.ai_tokens_in += tokens_in
    counter.ai_tokens_out += tokens_out


def settings_limits() -> dict[str, int]:
    """Global ceilings from configuration, applied on top of the plan limits."""
    settings = get_settings()
    return {
        "competitors": settings.quota_competitors,
        "analyses_per_month": settings.quota_analyses_per_month,
        "pages_per_month": settings.quota_pages_per_month,
    }


__all__ = [
    "PLAN_LIMITS",
    "UsageSummary",
    "accept_invitation",
    "assert_can_add_competitor",
    "assert_can_run_analysis",
    "change_member_role",
    "create_organization",
    "current_period",
    "get_membership",
    "get_usage",
    "invite_member",
    "limits_for",
    "list_for_user",
    "list_members",
    "record_usage",
    "remove_member",
    "slugify",
    "update_organization",
]
