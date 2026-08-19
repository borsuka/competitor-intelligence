"""Application error taxonomy and HTTP problem responses.

Services raise :class:`AppError` subclasses; they know nothing about HTTP.  The
exception handlers registered in ``app.main`` translate them into a stable JSON body:

.. code-block:: json

    {"error": {"code": "competitor_not_found", "message": "...", "details": {...},
               "request_id": "..."}}

Client code switches on ``code`` — a machine-readable string that never changes — not on
the human-readable ``message``.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected failure in the application."""

    status_code: int = 400
    code: str = "bad_request"
    message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        if request_id:
            error["request_id"] = request_id
        return {"error": error}


# --------------------------------------------------------------------- 4xx


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "The submitted data is invalid."


class AuthenticationError(AppError):
    status_code = 401
    code = "not_authenticated"
    message = "Authentication is required."


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    # Deliberately does not say whether the email exists — that is a user enumeration
    # oracle.
    message = "Incorrect email or password."


class TokenError(AuthenticationError):
    code = "invalid_token"
    message = "The token is invalid or has expired."


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource does not exist."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The resource already exists or is in a conflicting state."


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please slow down."

    def __init__(self, retry_after: int = 60, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.retry_after = retry_after
        self.details.setdefault("retry_after_seconds", retry_after)


class QuotaExceededError(AppError):
    status_code = 429
    code = "quota_exceeded"
    message = "Your organization has reached its plan limit for this resource."


class CSRFError(AppError):
    status_code = 403
    code = "csrf_failed"
    message = "CSRF token missing or invalid."


# --------------------------------------------------------------------- 5xx / domain


class ExternalServiceError(AppError):
    status_code = 502
    code = "external_service_error"
    message = "An upstream service failed."


class UnsafeURLError(ValidationError):
    """Raised by the SSRF guard.  Never leaks the resolved IP back to the caller."""

    code = "unsafe_url"
    message = "That URL cannot be fetched."


class FetchError(ExternalServiceError):
    code = "fetch_failed"
    message = "The competitor website could not be fetched."


class AIProviderError(ExternalServiceError):
    code = "ai_provider_error"
    message = "The AI provider request failed."


class AIResponseInvalidError(AppError):
    """The provider replied, but the payload did not satisfy the schema."""

    status_code = 502
    code = "ai_response_invalid"
    message = "The AI response did not match the expected structure."


class JobError(AppError):
    status_code = 500
    code = "job_failed"
    message = "The background job failed."


__all__ = [
    "AIProviderError",
    "AIResponseInvalidError",
    "AppError",
    "AuthenticationError",
    "CSRFError",
    "ConflictError",
    "ExternalServiceError",
    "FetchError",
    "InvalidCredentialsError",
    "JobError",
    "NotFoundError",
    "PermissionDeniedError",
    "QuotaExceededError",
    "RateLimitError",
    "TokenError",
    "UnsafeURLError",
    "ValidationError",
]
