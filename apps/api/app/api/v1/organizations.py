"""Organization, membership and usage endpoints."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, status

from app.api.deps import AdminScope, CurrentUser, Scope, SessionDep
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models.identity import Organization, User
from app.schemas.auth import AcceptInvitationRequest
from app.schemas.common import MessageResponse
from app.schemas.competitor import (
    InviteRequest,
    InviteResponse,
    MemberResponse,
    OrganizationResponse,
    OrganizationUpdate,
    RoleUpdate,
    UsageResponse,
)
from app.services import email as email_service
from app.services import organizations as org_service

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.get("/{organization_id}", response_model=OrganizationResponse)
async def get_organization(session: SessionDep, scope: Scope) -> OrganizationResponse:
    organization = await session.get(Organization, scope.organization_id)
    if organization is None or organization.deleted_at is not None:
        raise NotFoundError("Organization not found.", code="organization_not_found")
    return OrganizationResponse.model_validate(organization)


@router.patch("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    payload: OrganizationUpdate, session: SessionDep, scope: AdminScope
) -> OrganizationResponse:
    organization = await org_service.update_organization(
        session, scope, **payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return OrganizationResponse.model_validate(organization)


@router.get("/{organization_id}/usage", response_model=UsageResponse)
async def get_usage(session: SessionDep, scope: Scope) -> UsageResponse:
    usage = await org_service.get_usage(session, scope.organization_id)
    await session.commit()  # get_usage may create the period's counter row
    return UsageResponse(**asdict(usage))


@router.get("/{organization_id}/members", response_model=list[MemberResponse])
async def list_members(session: SessionDep, scope: Scope) -> list[MemberResponse]:
    members = await org_service.list_members(session, scope)
    return [
        MemberResponse(
            id=membership.id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            joined_at=membership.created_at,
        )
        for membership, user in members
    ]


@router.post(
    "/{organization_id}/invitations",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    payload: InviteRequest, session: SessionDep, scope: AdminScope
) -> InviteResponse:
    invitation, token = await org_service.invite_member(
        session, scope, email=payload.email, role=payload.role
    )
    organization = await session.get(Organization, scope.organization_id)
    inviter = await session.get(User, scope.user_id)
    await session.commit()

    email_service.queue(
        "invitation",
        to=invitation.email,
        organization_name=organization.name if organization else "a workspace",
        inviter_name=inviter.full_name if inviter else None,
        token=token,
    )

    settings = get_settings()
    # Same rule as the verification token: shown only when nothing can deliver it.
    expose_token = not settings.is_production and not email_service.transport_is_configured()

    return InviteResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invite_token=token if expose_token else None,
    )


@router.post("/invitations/accept", response_model=MessageResponse)
async def accept_invitation(
    payload: AcceptInvitationRequest, session: SessionDep, user: CurrentUser
) -> MessageResponse:
    await org_service.accept_invitation(session, token=payload.token, user=user)
    await session.commit()
    return MessageResponse(message="You have joined the organization.")


@router.patch("/{organization_id}/members/{member_id}", response_model=MemberResponse)
async def change_role(
    member_id: uuid.UUID, payload: RoleUpdate, session: SessionDep, scope: AdminScope
) -> MemberResponse:
    membership = await org_service.change_member_role(
        session, scope, member_id=member_id, role=payload.role
    )
    await session.commit()

    # Loaded explicitly: Membership.user is a lazy relationship, and touching it here
    # would trigger IO outside the greenlet context asyncpg needs.
    user = await session.get(User, membership.user_id)
    return MemberResponse(
        id=membership.id,
        user_id=membership.user_id,
        email=user.email if user else "",
        full_name=user.full_name if user else "",
        role=membership.role,
        joined_at=membership.created_at,
    )


@router.delete("/{organization_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(member_id: uuid.UUID, session: SessionDep, scope: AdminScope) -> None:
    await org_service.remove_member(session, scope, member_id=member_id)
    await session.commit()


__all__ = ["router"]
