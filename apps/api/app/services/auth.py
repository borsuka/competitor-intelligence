"""Authentication.

Two responsibilities kept deliberately apart:

* **verifying an identity** — today only email + password, tomorrow possibly OAuth,
* **issuing a session** — :func:`issue_session`, which knows nothing about how the
  identity was established.

Adding a social login therefore means writing one function that returns a verified
:class:`User`; the session machinery, rotation and revocation stay untouched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, InvalidCredentialsError, TokenError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    create_token,
    decode_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.core.tenancy import Role
from app.db.base import utcnow
from app.db.models.enums import VerificationPurpose
from app.db.models.identity import (
    Membership,
    Organization,
    RefreshToken,
    User,
    VerificationToken,
)
from app.services import audit, organizations

log = get_logger(__name__)

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200


@dataclass(slots=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(slots=True)
class RegistrationResult:
    user: User
    organization: Organization
    tokens: SessionTokens
    verification_token: str


def normalize_email(email: str) -> str:
    """Lower-case and trim.

    Storing the normalised form is what makes the unique index case-insensitive without
    the citext extension, and it must be applied on every read path too.
    """
    return email.strip().lower()


def validate_password(password: str) -> None:
    """Length-first policy.

    Deliberately no character-class rules: they push users toward ``Password1!`` while a
    long passphrase is both stronger and easier to remember.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"The password must be at least {MIN_PASSWORD_LENGTH} characters long.",
            code="password_too_short",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError("That password is too long.", code="password_too_long")


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


# ------------------------------------------------------------------- sessions


async def issue_session(
    session: AsyncSession,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> SessionTokens:
    """Mint an access/refresh pair and record the refresh token server-side."""
    settings = get_settings()

    access_token, _, access_expires = create_token(str(user.id), "access")
    refresh_token, refresh_jti, refresh_expires = create_token(str(user.id), "refresh")

    session.add(
        RefreshToken(
            user_id=user.id,
            jti=refresh_jti,
            token_hash=hash_opaque_token(refresh_token),
            family_id=family_id or uuid.uuid4(),
            expires_at=refresh_expires,
            user_agent=(user_agent or "")[:255] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
    )
    _ = settings  # settings drive the TTLs inside create_token

    return SessionTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_expires,
        refresh_expires_at=refresh_expires,
    )


async def rotate_session(
    session: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[User, SessionTokens]:
    """Exchange a refresh token for a new pair, invalidating the old one.

    Replaying a token that was already used revokes the whole family.  That is the
    standard detection for a stolen refresh token: the thief's use and the victim's use
    cannot both be the first, so one of them trips the alarm and both sessions end.
    """
    payload = decode_token(refresh_token, "refresh")
    token_hash = hash_opaque_token(refresh_token)

    result = await session.execute(select(RefreshToken).where(RefreshToken.jti == payload["jti"]))
    stored = result.scalar_one_or_none()

    if stored is None or stored.token_hash != token_hash:
        raise TokenError()

    now = utcnow()
    if stored.revoked_at is not None or stored.expires_at <= now:
        raise TokenError()

    if stored.used_at is not None:
        log.warning(
            "auth.refresh_token_reuse_detected",
            user_id=str(stored.user_id),
            family_id=str(stored.family_id),
        )
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        raise TokenError(
            "This session has been revoked. Please sign in again.", code="token_reused"
        )

    stored.used_at = now
    stored.revoked_at = now

    user = await session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise TokenError()

    tokens = await issue_session(
        session,
        user,
        family_id=stored.family_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return user, tokens


async def revoke_session(session: AsyncSession, refresh_token: str) -> None:
    """Sign out. A token that cannot be decoded is treated as already gone."""
    try:
        payload = decode_token(refresh_token, "refresh")
    except TokenError:
        return
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.jti == payload["jti"], RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


# -------------------------------------------------------------- registration


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> RegistrationResult:
    """Create a user, their first organization, and a session.

    A user without an organization has nowhere to put competitors, so the two are created
    in one transaction rather than leaving a half-provisioned account behind.
    """
    normalized = normalize_email(email)
    validate_password(password)

    if await get_user_by_email(session, normalized) is not None:
        # The email is already the unique key, so this is not new information to an
        # attacker who can simply try to register. Returning 409 keeps the UX sane.
        raise ConflictError("An account with that email already exists.", code="email_taken")

    user = User(
        email=normalized,
        hashed_password=hash_password(password),
        full_name=full_name.strip()[:120],
        is_active=True,
    )
    session.add(user)
    await session.flush()

    organization = await organizations.create_organization(
        session,
        name=organization_name or f"{user.full_name.split(' ')[0]}'s workspace",
        owner=user,
    )

    tokens = await issue_session(session, user, user_agent=user_agent, ip_address=ip_address)
    raw_token = await create_verification_token(
        session, user, VerificationPurpose.EMAIL_VERIFICATION
    )

    await audit.record(
        session,
        action="user.registered",
        organization_id=organization.id,
        actor_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return RegistrationResult(
        user=user, organization=organization, tokens=tokens, verification_token=raw_token
    )


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[User, SessionTokens]:
    """Verify credentials and start a session."""
    user = await get_user_by_email(session, email)

    if user is None:
        # Hash anyway so that a missing account and a wrong password take the same time.
        hash_password(password)
        raise InvalidCredentialsError()

    if not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()

    if not user.is_active:
        raise InvalidCredentialsError("This account has been disabled.", code="account_disabled")

    # Transparent upgrade when the hashing policy has been strengthened since signup.
    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)

    user.last_login_at = utcnow()
    tokens = await issue_session(session, user, user_agent=user_agent, ip_address=ip_address)

    await audit.record(
        session,
        action="user.logged_in",
        actor_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user, tokens


# -------------------------------------------------- verification / reset flows


async def create_verification_token(
    session: AsyncSession, user: User, purpose: VerificationPurpose
) -> str:
    """Issue a single-use token and store only its hash.

    Returns the raw token, which the caller emails.  It is never stored and cannot be
    recovered — a database read gives an attacker nothing replayable.
    """
    settings = get_settings()
    ttl = (
        settings.verification_token_ttl_seconds
        if purpose is VerificationPurpose.EMAIL_VERIFICATION
        else settings.password_reset_token_ttl_seconds
    )

    # Invalidate outstanding tokens for the same purpose: two live reset links is one
    # more than necessary.
    await session.execute(
        update(VerificationToken)
        .where(
            VerificationToken.user_id == user.id,
            VerificationToken.purpose == purpose,
            VerificationToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )

    raw = generate_opaque_token()
    session.add(
        VerificationToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_opaque_token(raw),
            expires_at=utcnow() + timedelta(seconds=ttl),
        )
    )
    return raw


async def _consume_verification_token(
    session: AsyncSession, token: str, purpose: VerificationPurpose
) -> User:
    result = await session.execute(
        select(VerificationToken).where(
            VerificationToken.token_hash == hash_opaque_token(token),
            VerificationToken.purpose == purpose,
        )
    )
    stored = result.scalar_one_or_none()
    now = utcnow()
    if stored is None or stored.used_at is not None or stored.expires_at <= now:
        raise TokenError("That link is invalid or has expired.", code="token_invalid")

    stored.used_at = now
    user = await session.get(User, stored.user_id)
    if user is None:
        raise TokenError()
    return user


async def verify_email(session: AsyncSession, token: str) -> User:
    user = await _consume_verification_token(session, token, VerificationPurpose.EMAIL_VERIFICATION)
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    await audit.record(
        session,
        action="user.email_verified",
        actor_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    return user


async def request_password_reset(session: AsyncSession, email: str) -> tuple[User, str] | None:
    """Start a reset.

    Returns ``None`` for an unknown address, and the caller responds identically either
    way — otherwise the endpoint becomes a way to enumerate registered emails.
    """
    user = await get_user_by_email(session, email)
    if user is None or not user.is_active:
        return None
    token = await create_verification_token(session, user, VerificationPurpose.PASSWORD_RESET)
    return user, token


async def reset_password(session: AsyncSession, *, token: str, new_password: str) -> User:
    validate_password(new_password)
    user = await _consume_verification_token(session, token, VerificationPurpose.PASSWORD_RESET)

    user.hashed_password = hash_password(new_password)
    # A password reset is the standard response to "my account was compromised", so every
    # other session must end.
    await revoke_all_sessions(session, user.id)

    await audit.record(
        session,
        action="user.password_reset",
        actor_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    return user


async def change_password(
    session: AsyncSession, user: User, *, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.hashed_password):
        raise InvalidCredentialsError("The current password is incorrect.")
    validate_password(new_password)
    user.hashed_password = hash_password(new_password)
    await revoke_all_sessions(session, user.id)
    await audit.record(
        session,
        action="user.password_changed",
        actor_user_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )


async def load_memberships(session: AsyncSession, user_id: uuid.UUID) -> list[Membership]:
    result = await session.execute(
        select(Membership)
        .where(Membership.user_id == user_id)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Organization.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def cleanup_expired_tokens(session: AsyncSession) -> int:
    """Housekeeping for the scheduler: drop tokens that can no longer be used."""
    from sqlalchemy import delete

    cutoff = datetime.now(UTC) - timedelta(days=7)
    refresh_result = await session.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
    )
    verification_result = await session.execute(
        delete(VerificationToken).where(VerificationToken.expires_at < cutoff)
    )
    return int(refresh_result.rowcount or 0) + int(verification_result.rowcount or 0)


DEFAULT_ROLE = Role.MEMBER

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "RegistrationResult",
    "SessionTokens",
    "authenticate",
    "change_password",
    "cleanup_expired_tokens",
    "create_verification_token",
    "get_user_by_email",
    "issue_session",
    "load_memberships",
    "normalize_email",
    "register",
    "request_password_reset",
    "reset_password",
    "revoke_all_sessions",
    "revoke_session",
    "rotate_session",
    "validate_password",
    "verify_email",
]
