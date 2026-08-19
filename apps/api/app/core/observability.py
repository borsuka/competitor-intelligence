"""Error tracking and tracing.

Both are opt-in and both are optional dependencies: setting `SENTRY_DSN` or
`OTEL_EXPORTER_OTLP_ENDPOINT` turns them on, and installing the `observability` extra
provides the packages. Nothing here is imported at module scope, so a deployment that
wants neither carries neither.

    pip install -e ".[observability]"

The reason this is a module rather than three lines in `main.py`: the workers need the
same setup, and an error tracker configured in one process but not the other is worse
than none, because it produces a false sense of coverage.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Mirrors the log redaction list. Sentry captures request data and local variables, which
# is exactly where a token ends up if nobody stops it.
_SENSITIVE_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "pepper",
    "credential",
    "csrf",
)
_SAFE_KEYS = frozenset({"tokens_in", "tokens_out", "total_tokens", "token_count"})


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively replace values whose key looks sensitive."""
    if depth > 6:
        return value
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if name not in _SAFE_KEYS and any(part in name for part in _SENSITIVE_PARTS):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = _scrub(item, depth + 1)
        return cleaned
    if isinstance(value, list):
        return [_scrub(item, depth + 1) for item in value]
    return value


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Last line of defence before an event leaves the process."""
    for section in ("request", "extra", "contexts"):
        if section in event:
            event[section] = _scrub(event[section])
    return event


def configure_sentry(component: str) -> bool:
    """Initialise Sentry when a DSN is configured. Returns whether it was enabled."""
    settings = get_settings()
    if not settings.sentry_dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        # A configured DSN with no package is a misconfiguration worth shouting about:
        # the operator believes errors are being captured, and they are not.
        log.warning(
            "observability.sentry_unavailable",
            detail="SENTRY_DSN is set but sentry-sdk is not installed. "
            'Install the "observability" extra.',
        )
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        release=settings.app_name.lower() + "@0.1.0",
        # Traces are sampled; errors are not. A 10% trace sample is enough to see latency
        # shape without paying for every request.
        traces_sample_rate=0.1 if settings.is_production else 1.0,
        # This product handles competitor data on behalf of tenants. Sending user
        # identifiers and request bodies to a third party by default is not a decision to
        # make implicitly.
        send_default_pii=False,
        before_send=_before_send,
        max_request_body_size="never",
    )
    sentry_sdk.set_tag("component", component)
    log.info("observability.sentry_enabled", component=component)
    return True


def configure_tracing(component: str, app: Any = None) -> bool:
    """Initialise OpenTelemetry when an OTLP endpoint is configured."""
    settings = get_settings()
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "observability.tracing_unavailable",
            detail="OTEL_EXPORTER_OTLP_ENDPOINT is set but the OpenTelemetry packages "
            'are not installed. Install the "observability" extra.',
        )
        return False

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": f"{settings.app_name.lower()}-{component}",
                "deployment.environment": settings.environment,
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # Instrumentation is best-effort: a missing instrumentation package should cost
    # detail, not startup.
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        except ImportError:
            log.info("observability.fastapi_instrumentation_unavailable")

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(tracer_provider=provider)
    except ImportError:
        log.info("observability.sqlalchemy_instrumentation_unavailable")

    log.info("observability.tracing_enabled", component=component, endpoint=endpoint)
    return True


def configure_observability(component: str, app: Any = None) -> dict[str, bool]:
    """Set up both, for either the API or a worker."""
    return {
        "sentry": configure_sentry(component),
        "tracing": configure_tracing(component, app),
    }


__all__ = ["configure_observability", "configure_sentry", "configure_tracing"]
