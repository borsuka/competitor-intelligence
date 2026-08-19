"""Security primitives: password hashing, JWTs, opaque token handling.

Everything here is pure — no database, no request objects — so it is trivially testable
and cannot accidentally acquire a dependency on the web layer.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import TokenError

TokenType = Literal["access", "refresh"]
JWT_ALGORITHM = "HS256"

# Argon2id with parameters that cost ~50ms on a modern server core.  Raising the memory
# cost is the cheapest way to hurt GPU cracking, so it is the knob that is turned up.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


# --------------------------------------------------------------------- passwords


def _peppered(password: str) -> str:
    """Append the deployment pepper.

    The pepper lives in the environment, not the database, so a stolen database dump is
    not enough to start cracking hashes offline.
    """
    pepper = get_settings().password_pepper.get_secret_value()
    return f"{password}{pepper}" if pepper else password


def hash_password(password: str) -> str:
    return _hasher.hash(_peppered(password))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, _peppered(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ------------------------------------------------------------------------ JWT


def _now() -> datetime:
    return datetime.now(UTC)


def create_token(
    subject: str,
    token_type: TokenType,
    *,
    expires_in: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, str, datetime]:
    """Mint a signed JWT.

    Returns ``(encoded_token, jti, expires_at)``.  The ``jti`` is returned separately so
    that refresh tokens can be tracked (and revoked) server-side by id.
    """
    settings = get_settings()
    if expires_in is None:
        expires_in = (
            settings.access_token_ttl_seconds
            if token_type == "access"  # noqa: S105 - a token kind, not a credential
            else settings.refresh_token_ttl_seconds
        )

    issued_at = _now()
    expires_at = issued_at + timedelta(seconds=expires_in)
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "jti": jti,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.app_name,
    }
    if extra_claims:
        payload.update(extra_claims)

    encoded = jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)
    return encoded, jti, expires_at


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT, or raise :class:`TokenError`.

    ``expected_type`` is checked explicitly: without it a refresh token would be
    accepted anywhere an access token is, which is a privilege escalation.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[JWT_ALGORITHM],
            issuer=settings.app_name,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("The session has expired.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError() from exc

    if payload.get("typ") != expected_type:
        raise TokenError()
    return payload


# ---------------------------------------------------------------- opaque tokens


def generate_opaque_token(length: int = 48) -> str:
    """A URL-safe random token for email verification / password reset / invites."""
    return secrets.token_urlsafe(length)


def hash_opaque_token(token: str) -> str:
    """SHA-256 of a token, for storage.

    Opaque tokens are high-entropy random strings, so a fast hash is the right tool —
    there is nothing to brute force.  Storing the hash means a database read cannot be
    replayed as a valid token.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time comparison, so timing cannot reveal a prefix match."""
    return hmac.compare_digest(left, right)


# ------------------------------------------------------------------------ CSRF


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


__all__ = [
    "TokenType",
    "create_token",
    "decode_token",
    "generate_csrf_token",
    "generate_opaque_token",
    "hash_opaque_token",
    "hash_password",
    "password_needs_rehash",
    "tokens_equal",
    "verify_password",
]
