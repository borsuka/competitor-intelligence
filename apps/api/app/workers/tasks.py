"""Celery tasks.

Tasks are thin: open a session, call a service, record the outcome.  All logic lives in
:mod:`app.services`, which is what lets the same pipeline be exercised by a test without
a broker.

Celery is synchronous and the services are async, so each task runs its coroutine with
``asyncio.run``.  A fresh event loop per task is correct here — tasks are long-lived and
the setup cost is irrelevant next to a crawl.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from celery import Task

from app.core.errors import AppError, QuotaExceededError
from app.core.logging import bind_context, clear_context, get_logger
from app.db.base import utcnow
from app.db.session import dispose_engine, session_scope
from app.workers.celery_app import celery_app

log = get_logger(__name__)

# Failures worth retrying: the site was briefly unreachable, the provider rate-limited
# us, the database blipped.  A validation failure is not here — retrying it three times
# just delays the error the user needs to see.
RETRYABLE_ERROR_CODES = frozenset(
    {
        "fetch_timeout",
        "fetch_connection_error",
        "external_service_error",
        "ai_provider_error",
        "rate_limited",
    }
)


def _run(coro) -> Any:
    """Run a coroutine and leave no connection behind.

    A worker process handles many tasks in sequence, and ``asyncio.run`` creates a fresh
    event loop for each one. The SQLAlchemy engine is cached process-wide, so without
    this the second task would inherit a pool of connections created in the *first*
    task's loop and fail with "got Future attached to a different loop".

    Disposing inside the same loop that created the connections is what makes the engine
    safe to re-create on the next task.
    """

    async def runner() -> Any:
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(runner())


@celery_app.task(
    bind=True,
    name="sentinel.run_analysis",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(),
)
def run_analysis_task(self: Task, job_id: str) -> dict[str, Any]:
    """Run one competitor analysis."""
    bind_context(task_id=self.request.id, job_id=job_id)
    try:
        return _run(_run_analysis(uuid.UUID(job_id), self))
    finally:
        clear_context()


async def _run_analysis(job_id: uuid.UUID, task: Task) -> dict[str, Any]:
    from app.db.models.analysis import AnalysisJob
    from app.services import analysis as analysis_service

    async with session_scope() as session:
        # Record which Celery task owns this job, so it can be revoked from the API.
        job = await session.get(AnalysisJob, job_id)
        if job is not None and task.request.id:
            job.celery_task_id = task.request.id
            await session.commit()

        try:
            outcome = await analysis_service.run_analysis(session, job_id=job_id)
        except QuotaExceededError:
            # Terminal by definition: retrying cannot create quota.
            log.warning("task.analysis_quota_exceeded", job_id=str(job_id))
            return {"job_id": str(job_id), "status": "failed", "reason": "quota_exceeded"}
        except AppError as exc:
            if exc.code in RETRYABLE_ERROR_CODES and task.request.retries < 3:
                # Exponential backoff with jitter, applied by Celery's countdown.
                countdown = min(600, 60 * (2**task.request.retries))
                log.info(
                    "task.analysis_retry",
                    job_id=str(job_id),
                    attempt=task.request.retries + 1,
                    countdown=countdown,
                    error_code=exc.code,
                )
                raise task.retry(exc=exc, countdown=countdown) from exc
            log.warning("task.analysis_failed", job_id=str(job_id), error_code=exc.code)
            return {"job_id": str(job_id), "status": "failed", "reason": exc.code}

        return {
            "job_id": str(job_id),
            "status": outcome.status.value,
            "analysis_id": str(outcome.analysis_id) if outcome.analysis_id else None,
            "changes_detected": outcome.changes_detected,
            "pages_crawled": outcome.pages_crawled,
            "reused_previous": outcome.reused_previous,
        }


@celery_app.task(name="sentinel.enqueue_due_monitoring")
def enqueue_due_monitoring_task() -> dict[str, Any]:
    """Scheduler tick: queue refreshes for competitors whose interval has elapsed."""
    return _run(_enqueue_due_monitoring())


async def _enqueue_due_monitoring() -> dict[str, Any]:
    from app.services import analysis as analysis_service

    async with session_scope() as session:
        job_ids = await analysis_service.refresh_due_competitors(session)

    for index, job_id in enumerate(job_ids):
        # Spread the batch across the interval instead of firing every job at once: 500
        # simultaneous crawls would saturate the workers and look like an attack to the
        # sites being crawled.
        run_analysis_task.apply_async(args=[str(job_id)], countdown=index * 20)

    log.info("task.monitoring_enqueued", count=len(job_ids))
    return {"enqueued": len(job_ids)}


@celery_app.task(name="sentinel.cleanup_expired_tokens")
def cleanup_expired_tokens_task() -> dict[str, int]:
    return _run(_cleanup_expired_tokens())


async def _cleanup_expired_tokens() -> dict[str, int]:
    from app.services import auth as auth_service

    async with session_scope() as session:
        removed = await auth_service.cleanup_expired_tokens(session)
        await session.commit()
    log.info("task.tokens_cleaned", removed=removed)
    return {"removed": removed}


@celery_app.task(name="sentinel.requeue_stuck_jobs")
def requeue_stuck_jobs_task() -> dict[str, int]:
    """Recover jobs abandoned by a worker that died mid-run.

    ``task_acks_late`` handles redelivery for a clean crash, but a worker killed by the
    OOM killer can leave a row stuck in ``running`` forever. This sweep is the backstop.
    """
    return _run(_requeue_stuck_jobs())


async def _requeue_stuck_jobs() -> dict[str, int]:
    from sqlalchemy import select

    from app.db.models.analysis import AnalysisJob
    from app.db.models.enums import JobStatus

    cutoff = utcnow() - timedelta(minutes=45)
    requeued = 0

    async with session_scope() as session:
        result = await session.execute(
            select(AnalysisJob).where(
                AnalysisJob.status == JobStatus.RUNNING,
                AnalysisJob.started_at < cutoff,
            )
        )
        for job in result.scalars().all():
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.error_code = "job_abandoned"
                job.error_message = "The job stopped responding and exhausted its retry budget."
                job.finished_at = utcnow()
                continue
            job.status = JobStatus.PENDING
            job.stage = "queued"
            job.progress = 0
            requeued += 1
        await session.commit()

        pending = await session.execute(
            select(AnalysisJob.id).where(AnalysisJob.status == JobStatus.PENDING).limit(50)
        )
        pending_ids = list(pending.scalars().all())

    for index, job_id in enumerate(pending_ids):
        run_analysis_task.apply_async(args=[str(job_id)], countdown=index * 10)

    log.info("task.stuck_jobs_requeued", requeued=requeued)
    return {"requeued": requeued}


__all__ = [
    "cleanup_expired_tokens_task",
    "enqueue_due_monitoring_task",
    "requeue_stuck_jobs_task",
    "run_analysis_task",
]
