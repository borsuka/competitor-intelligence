"""Authentication endpoints.

Tokens are set as cookies and never appear in a response body.  The access and refresh
cookies are ``HttpOnly`` so JavaScript cannot read them; the CSRF cookie deliberately is
not, because the frontend has to echo it in a header.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Request, Response, status

from app.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    SessionDep,
    client_ip,
    user_agent,
)
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.security import generate_csrf_token
from app.schemas.auth import (
    EmailVerificationRequest,
    LoginRequest,
    OrganizationSummary,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    SessionResponse,
    UserResponse,
)
from app.schemas.common import MessageResponse
from app.services import auth as auth_service
from app.services import email as email_service
from app.services import organizations as org_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, tokens: auth_service.SessionTokens) -> None:
    settings = get_settings()
    common = {
        "domain": settings.cookie_domain,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }

    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        **common,
    )
    # Readable by design: the client copies it into the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        generate_csrf_token(),
        max_age=settings.refresh_token_ttl_seconds,
        httponly=False,
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, domain=settings.cookie_domain, path="/")


async def _session_payload(session, user) -> SessionResponse:
    memberships = await org_service.list_for_user(session, user.id)
    return SessionResponse(
        user=UserResponse.model_validate(user),
        organizations=[
            OrganizationSummary(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                plan=organization.plan,
                role=role,
            )
            for organization, role in memberships
        ],
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> RegisterResponse:
    result = await auth_service.register(
        session,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        organization_name=payload.organization_name,
        user_agent=user_agent(request),
        ip_address=client_ip(request),
    )
    await session.commit()

    _set_auth_cookies(response, result.tokens)
    body = await _session_payload(session, result.user)

    email_service.queue(
        "verification",
        to=result.user.email,
        name=result.user.full_name,
        token=result.verification_token,
    )

    settings = get_settings()
    # The token is returned only when nothing can deliver it. Once a mail transport is
    # configured, echoing it in the response would be a second, unnecessary copy of a
    # credential — and one that ends up in browser history and proxy logs.
    expose_token = not settings.is_production and not email_service.transport_is_configured()

    return RegisterResponse(
        user=body.user,
        organizations=body.organizations,
        verification_token=result.verification_token if expose_token else None,
    )


@router.post("/login", response_model=SessionResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> SessionResponse:
    user, tokens = await auth_service.authenticate(
        session,
        email=payload.email,
        password=payload.password,
        user_agent=user_agent(request),
        ip_address=client_ip(request),
    )
    await session.commit()
    _set_auth_cookies(response, tokens)
    return await _session_payload(session, user)


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> SessionResponse:
    if not refresh_token:
        raise AuthenticationError()

    user, tokens = await auth_service.rotate_session(
        session,
        refresh_token,
        user_agent=user_agent(request),
        ip_address=client_ip(request),
    )
    await session.commit()
    _set_auth_cookies(response, tokens)
    return await _session_payload(session, user)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    session: SessionDep,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> MessageResponse:
    if refresh_token:
        await auth_service.revoke_session(session, refresh_token)
        await session.commit()
    _clear_auth_cookies(response)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=SessionResponse)
async def me(session: SessionDep, user: CurrentUser) -> SessionResponse:
    return await _session_payload(session, user)


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(payload: EmailVerificationRequest, session: SessionDep) -> MessageResponse:
    await auth_service.verify_email(session, payload.token)
    await session.commit()
    return MessageResponse(message="Your email address has been verified.")


@router.post("/password-reset", response_model=MessageResponse)
async def request_password_reset(
    payload: PasswordResetRequest, session: SessionDep
) -> MessageResponse:
    """Always reports success.

    Saying "no account with that email" here would turn this endpoint into a user
    enumeration oracle, so the response is identical either way.
    """
    result = await auth_service.request_password_reset(session, payload.email)
    await session.commit()

    if result is not None:
        user, token = result
        email_service.queue("password_reset", to=user.email, name=user.full_name, token=token)

    return MessageResponse(
        message="If an account exists for that address, a reset link has been sent."
    )


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def confirm_password_reset(
    payload: PasswordResetConfirm, response: Response, session: SessionDep
) -> MessageResponse:
    await auth_service.reset_password(
        session, token=payload.token, new_password=payload.new_password
    )
    await session.commit()
    _clear_auth_cookies(response)
    return MessageResponse(message="Your password has been changed. Please sign in again.")


@router.post("/password", response_model=MessageResponse)
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
) -> MessageResponse:
    await auth_service.change_password(
        session,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    await session.commit()
    _clear_auth_cookies(response)
    return MessageResponse(message="Your password has been changed. Please sign in again.")


__all__ = ["router"]
