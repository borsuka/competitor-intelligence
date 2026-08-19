"""HTTP middleware: request context, security headers, CSRF, rate limiting."""

from __future__ import annotations

import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import CSRFError, RateLimitError
from app.core.logging import bind_context, clear_context, get_logger
from app.core.security import tokens_equal

log = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Endpoints that establish a session, so no CSRF cookie exists yet.  They are safe
# because they require credentials that an attacker's cross-site form does not have.
CSRF_EXEMPT_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
        "/api/v1/auth/password-reset",
        "/api/v1/auth/password-reset/confirm",
        "/api/v1/auth/verify-email",
    }
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds logging context and times the request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", "")[:64] or str(uuid.uuid4())
        request.state.request_id = request_id

        clear_context()
        bind_context(request_id=request_id, path=request.url.path, method=request.method)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.exception("http.unhandled_error", duration_ms=duration_ms)
            raise
        finally:
            clear_context()

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        # Slow requests are the ones worth reading in a log; the rest are noise.
        if duration_ms > 1000 or response.status_code >= 500:
            log.warning(
                "http.slow_or_failed",
                status_code=response.status_code,
                duration_ms=duration_ms,
                path=request.url.path,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers.

    The API returns JSON, so the CSP is maximally restrictive: it exists to neuter a
    response that somehow ends up rendered as a document.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
        )
        if get_settings().is_production:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF protection.

    Sessions live in cookies, so the browser attaches them to cross-site requests
    automatically.  The defence is a token that exists in a readable cookie *and* must be
    echoed in a header — a cross-origin page can cause the cookie to be sent but cannot
    read it to set the header.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        from app.api.deps import CSRF_COOKIE, CSRF_HEADER

        if request.method in SAFE_METHODS or request.url.path in CSRF_EXEMPT_PATHS:
            return await call_next(request)

        # Requests without a session cookie cannot be CSRF: there is nothing to ride.
        if not request.cookies:
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get(CSRF_HEADER)

        if not cookie_token or not header_token or not tokens_equal(cookie_token, header_token):
            error = CSRFError()
            log.warning("http.csrf_rejected", path=request.url.path)
            return JSONResponse(
                status_code=error.status_code,
                content=error.to_payload(getattr(request.state, "request_id", None)),
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window rate limiting in Redis, keyed by route class and client.

    Fixed window rather than sliding: it is one atomic INCR plus one EXPIRE, and the
    boundary imprecision it allows does not matter for abuse protection.

    If Redis is unavailable the request is allowed through. That is a deliberate choice —
    an outage in the limiter should not take down the product — and it is why the limiter
    is a safety net, with quotas (enforced in Postgres) as the real spend control.
    """

    def __init__(self, app, redis_client=None) -> None:
        super().__init__(app)
        self._redis = redis_client

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)

        limit, window = self._limit_for(request)
        if limit is None:
            return await call_next(request)

        client = self._client_key(request)
        key = f"ratelimit:{request.method}:{self._route_class(request)}:{client}"

        try:
            redis = await self._get_redis()
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window)
            if count > limit:
                ttl = await redis.ttl(key)
                error = RateLimitError(retry_after=max(ttl, 1))
                log.info("http.rate_limited", path=request.url.path, client=client[:16])
                response = JSONResponse(
                    status_code=error.status_code,
                    content=error.to_payload(getattr(request.state, "request_id", None)),
                )
                response.headers["Retry-After"] = str(max(ttl, 1))
                return response
        except Exception as exc:
            log.warning("ratelimit.unavailable", error=str(exc)[:200])

        return await call_next(request)

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                get_settings().redis_url, encoding="utf-8", decode_responses=True
            )
        return self._redis

    @staticmethod
    def _client_key(request: Request) -> str:
        """Prefer the authenticated session over the IP.

        Rate limiting purely by IP punishes everyone behind one office NAT; the session
        cookie identifies the actual caller.
        """
        from app.api.deps import ACCESS_COOKIE, client_ip

        token = request.cookies.get(ACCESS_COOKIE)
        if token:
            # A prefix of the token, not the token itself: this key ends up in Redis.
            return f"session:{token[-32:]}"
        return f"ip:{client_ip(request) or 'unknown'}"

    @staticmethod
    def _route_class(request: Request) -> str:
        path = request.url.path
        if "/auth/" in path:
            return "auth"
        if path.endswith("/analyze") or "/comparisons" in path:
            return "analysis"
        if request.method in SAFE_METHODS:
            return "read"
        return "write"

    def _limit_for(self, request: Request) -> tuple[int | None, int]:
        settings = get_settings()
        route_class = self._route_class(request)
        return {
            "auth": (settings.rate_limit_auth_per_minute, 60),
            "analysis": (settings.rate_limit_analysis_per_hour, 3600),
            "write": (settings.rate_limit_write_per_minute, 60),
            "read": (settings.rate_limit_read_per_minute, 60),
        }.get(route_class, (None, 60))


__all__ = [
    "CSRF_EXEMPT_PATHS",
    "CSRFMiddleware",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
]
