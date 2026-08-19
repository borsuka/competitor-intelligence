"""ASGI application: middleware stack, exception handlers, lifespan."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import (
    CSRFMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.v1 import api_router, health
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine

log = get_logger(__name__)

DESCRIPTION = """
Sentinel turns a competitor's public website into structured, monitored intelligence.

**Provenance.** Responses distinguish observed facts from AI inference. Fields carry a
`source` of `observed` or `ai_inference`, and analyses produced without a configured AI
provider are flagged `is_mock`.

**Authentication.** Session cookies (`HttpOnly`). Unsafe methods additionally require the
`X-CSRF-Token` header, echoing the `sentinel_csrf` cookie.

**Tenancy.** Every organization-scoped route includes the organization id in its path.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging()
    log.info(
        "api.starting",
        environment=settings.environment,
        ai_provider=settings.ai_provider,
        js_rendering=settings.scraper_enable_js,
    )
    if settings.ai_provider == "mock" or not settings.anthropic_api_key:
        # Loud, once, at boot: everything downstream is labelled, but an operator should
        # not have to discover this from a badge in the UI.
        log.warning(
            "api.ai_provider_is_mock",
            detail=(
                "No AI provider key configured. Analyses will be labelled as development output."
            ),
        )

    yield

    await dispose_engine()
    log.info("api.stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=health.VERSION,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Order matters: the outermost middleware is added last, so this list runs
    # bottom-to-top on the way in.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,  # cookies must be sent cross-origin in development
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """One error shape for every failure, so clients switch on ``error.code``."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if exc.status_code >= 500:
            log.error("api.error", code=exc.code, status_code=exc.status_code)
        else:
            log.info("api.client_error", code=exc.code, status_code=exc.status_code)

        response = JSONResponse(status_code=exc.status_code, content=exc.to_payload(request_id))
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's raw errors are reshaped into field/message pairs: the frontend binds
        # them to form fields, and the raw structure leaks internal model names.
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                "message": error["msg"],
            }
            for error in exc.errors()[:20]
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Some of the submitted values are invalid.",
                    "details": {"fields": fields},
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            401: "not_authenticated",
            403: "permission_denied",
            404: "not_found",
            405: "method_not_allowed",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": codes.get(exc.status_code, "http_error"),
                    "message": str(exc.detail),
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Never echo the exception: internal messages leak table names, file paths and
        # occasionally credentials. The request id is how support correlates it.
        log.exception("api.unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Something went wrong on our side.",
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )


app = create_app()

__all__ = ["app", "create_app"]
