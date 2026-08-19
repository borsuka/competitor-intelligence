"""Authentication and identity contracts.

No schema in this module contains a token field: access and refresh tokens travel in
HttpOnly cookies and are never part of a JSON body, so a stray console.log or an error
report cannot leak a session.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.core.tenancy import Role
from app.db.models.enums import OrgPlan
from app.schemas.common import APIModel


class RegisterRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    full_name: str = Field(min_length=1, max_length=120)
    organization_name: str | None = Field(default=None, max_length=120)

    @field_validator("full_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Your name is required.")
        return cleaned


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class PasswordResetRequest(APIModel):
    email: EmailStr


class PasswordResetConfirm(APIModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class PasswordChangeRequest(APIModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class EmailVerificationRequest(APIModel):
    token: str = Field(min_length=10, max_length=200)


class AcceptInvitationRequest(APIModel):
    token: str = Field(min_length=10, max_length=200)


class OrganizationSummary(APIModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: OrgPlan
    role: Role


class UserResponse(APIModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime


class SessionResponse(APIModel):
    """What the client gets after login: who you are and where you can act."""

    user: UserResponse
    organizations: list[OrganizationSummary]


class RegisterResponse(SessionResponse):
    # Development affordance: without an email transport the verification link would be
    # unreachable, so it is returned outside production only. Never returned in production.
    verification_token: str | None = None


__all__ = [
    "AcceptInvitationRequest",
    "EmailVerificationRequest",
    "LoginRequest",
    "OrganizationSummary",
    "PasswordChangeRequest",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "RegisterRequest",
    "RegisterResponse",
    "SessionResponse",
    "UserResponse",
]
