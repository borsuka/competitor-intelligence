"""Celery application and schedule.

The worker imports the same models and services as the API — same codebase, different
process.  Nothing is duplicated, and a schema change cannot drift between them.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging, worker_process_init

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import configure_observability

log = get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "sentinel",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Acknowledge after the task finishes: if a worker dies mid-crawl the job is
    # redelivered rather than silently lost.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One task at a time per child. Analyses are long and memory-heavy; prefetching a
    # queue of them into one worker delays every other organization's jobs.
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,  # bounds any slow leak in a long-lived worker
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    result_expires=60 * 60 * 24,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
    task_routes={
        "sentinel.run_analysis": {"queue": "analysis"},
        "sentinel.*": {"queue": "default"},
    },
)

celery_app.conf.beat_schedule = {
    "enqueue-due-monitoring": {
        "task": "sentinel.enqueue_due_monitoring",
        "schedule": 15 * 60.0,  # every 15 minutes; each competitor has its own interval
    },
    "cleanup-expired-tokens": {
        "task": "sentinel.cleanup_expired_tokens",
        "schedule": 24 * 60 * 60.0,
    },
    "requeue-stuck-jobs": {
        "task": "sentinel.requeue_stuck_jobs",
        "schedule": 30 * 60.0,
    },
}


@setup_logging.connect
def _configure_celery_logging(**_kwargs) -> None:
    """Use the application's structured logging instead of Celery's default format."""
    configure_logging()
    # Workers need the same error tracking as the API. An analysis failing silently in a
    # background process is exactly the failure a tracker exists to catch.
    configure_observability("worker")


@worker_process_init.connect
def _reset_db_connections(**_kwargs) -> None:
    """Drop any engine inherited across the fork.

    A pooled connection shared between parent and child produces corrupted protocol
    state that is very hard to diagnose. The engine is lazy, so clearing it here means
    each child creates its own.
    """
    import asyncio
    import contextlib

    from app.db.session import dispose_engine

    with contextlib.suppress(RuntimeError):  # no loop to clean up
        asyncio.run(dispose_engine())


__all__ = ["celery_app"]
