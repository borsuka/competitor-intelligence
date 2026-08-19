"""Competitor endpoints, including analysis triggering and history."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import AdminScope, MemberScope, Scope, SessionDep
from app.db.models.analysis import AnalysisJob
from app.db.models.enums import CompetitorStatus, Importance, JobStatus, Severity
from app.schemas.common import Paginated, page_meta
from app.schemas.competitor import (
    AnalysisResponse,
    AnalyzeRequest,
    ChangeResponse,
    CompetitorCreate,
    CompetitorDetailResponse,
    CompetitorPageResponse,
    CompetitorResponse,
    CompetitorUpdate,
    JobResponse,
    PricingPlanResponse,
    ProductResponse,
    ScoreResponse,
)
from app.services import analysis as analysis_service
from app.services import competitors as competitor_service

router = APIRouter(prefix="/orgs/{organization_id}/competitors", tags=["competitors"])


@router.get("", response_model=Paginated[CompetitorResponse])
async def list_competitors(
    session: SessionDep,
    scope: Scope,
    search: Annotated[str | None, Query(max_length=120)] = None,
    status_filter: Annotated[CompetitorStatus | None, Query(alias="status")] = (
        CompetitorStatus.ACTIVE
    ),
    category: Annotated[str | None, Query(max_length=80)] = None,
    importance: Importance | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query(pattern="^(created_at|name|score|last_analyzed)$")] = "created_at",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[CompetitorResponse]:
    page = await competitor_service.list_competitors(
        session,
        scope,
        filters=competitor_service.CompetitorFilters(
            search=search,
            status=status_filter,
            category=category,
            importance=importance,
            tags=tags,
        ),
        limit=limit,
        offset=offset,
        sort=sort,
    )
    return Paginated(
        items=[CompetitorResponse.model_validate(item) for item in page.items],
        meta=page_meta(total=page.total, limit=limit, offset=offset, count=len(page.items)),
    )


@router.post("", response_model=CompetitorResponse, status_code=status.HTTP_201_CREATED)
async def create_competitor(
    payload: CompetitorCreate, session: SessionDep, scope: MemberScope
) -> CompetitorResponse:
    competitor = await competitor_service.create(
        session,
        scope,
        name=payload.name,
        website_url=payload.website_url,
        category=payload.category,
        tags=payload.tags,
        notes=payload.notes,
        importance=payload.importance,
    )

    if payload.analyze_now:
        # Quota may refuse the analysis; the competitor is still created, and the user
        # sees the quota error rather than losing the record they just added.
        try:
            job = await analysis_service.enqueue_analysis(
                session, scope, competitor_id=competitor.id
            )
            await session.commit()
            _dispatch(job.id)
        except Exception:
            await session.commit()
            raise
    else:
        await session.commit()

    return CompetitorResponse.model_validate(competitor)


@router.get("/{competitor_id}", response_model=CompetitorDetailResponse)
async def get_competitor(
    competitor_id: uuid.UUID, session: SessionDep, scope: Scope
) -> CompetitorDetailResponse:
    """Everything the detail page needs, in a fixed number of queries."""
    competitor = await competitor_service.get(session, scope, competitor_id)
    analysis = await competitor_service.latest_analysis(session, scope, competitor_id)
    score = await competitor_service.latest_score(session, scope, competitor_id)
    products = await competitor_service.current_products(session, scope, competitor_id)
    pricing = await competitor_service.current_pricing(session, scope, competitor_id)
    pages = await competitor_service.list_pages(session, scope, competitor_id)

    running_result = await session.execute(
        select(AnalysisJob)
        .where(
            AnalysisJob.competitor_id == competitor_id,
            AnalysisJob.organization_id == scope.organization_id,
            AnalysisJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
        .order_by(AnalysisJob.created_at.desc())
        .limit(1)
    )
    running = running_result.scalar_one_or_none()

    return CompetitorDetailResponse(
        competitor=CompetitorResponse.model_validate(competitor),
        analysis=AnalysisResponse.model_validate(analysis) if analysis else None,
        score=ScoreResponse.model_validate(score) if score else None,
        products=[ProductResponse.model_validate(product) for product in products],
        pricing=[PricingPlanResponse.model_validate(plan) for plan in pricing],
        pages=[CompetitorPageResponse.model_validate(page) for page in pages],
        running_job=JobResponse.model_validate(running) if running else None,
    )


@router.patch("/{competitor_id}", response_model=CompetitorResponse)
async def update_competitor(
    competitor_id: uuid.UUID,
    payload: CompetitorUpdate,
    session: SessionDep,
    scope: MemberScope,
) -> CompetitorResponse:
    competitor = await competitor_service.update(
        session, scope, competitor_id, **payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return CompetitorResponse.model_validate(competitor)


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competitor(
    competitor_id: uuid.UUID, session: SessionDep, scope: AdminScope
) -> None:
    await competitor_service.delete(session, scope, competitor_id)
    await session.commit()


@router.post(
    "/{competitor_id}/analyze", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def analyze(
    competitor_id: uuid.UUID,
    payload: AnalyzeRequest,
    session: SessionDep,
    scope: MemberScope,
) -> JobResponse:
    """Queue an analysis. Returns immediately with a job the client can poll."""
    job = await analysis_service.enqueue_analysis(
        session, scope, competitor_id=competitor_id, depth=payload.depth
    )
    await session.commit()
    _dispatch(job.id)
    return JobResponse.model_validate(job)


@router.get("/{competitor_id}/analyses", response_model=list[AnalysisResponse])
async def list_analyses(
    competitor_id: uuid.UUID,
    session: SessionDep,
    scope: Scope,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AnalysisResponse]:
    analyses = await competitor_service.list_analyses(session, scope, competitor_id, limit=limit)
    return [AnalysisResponse.model_validate(analysis) for analysis in analyses]


@router.get("/{competitor_id}/products", response_model=list[ProductResponse])
async def list_products(
    competitor_id: uuid.UUID, session: SessionDep, scope: Scope
) -> list[ProductResponse]:
    products = await competitor_service.current_products(session, scope, competitor_id)
    return [ProductResponse.model_validate(product) for product in products]


@router.get("/{competitor_id}/pricing", response_model=list[PricingPlanResponse])
async def list_pricing(
    competitor_id: uuid.UUID,
    session: SessionDep,
    scope: Scope,
    include_history: bool = False,
) -> list[PricingPlanResponse]:
    plans = (
        await competitor_service.pricing_history(session, scope, competitor_id)
        if include_history
        else await competitor_service.current_pricing(session, scope, competitor_id)
    )
    return [PricingPlanResponse.model_validate(plan) for plan in plans]


@router.get("/{competitor_id}/scores", response_model=list[ScoreResponse])
async def list_scores(
    competitor_id: uuid.UUID,
    session: SessionDep,
    scope: Scope,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[ScoreResponse]:
    scores = await competitor_service.score_history(session, scope, competitor_id, limit=limit)
    return [ScoreResponse.model_validate(score) for score in scores]


@router.get("/{competitor_id}/changes", response_model=Paginated[ChangeResponse])
async def list_competitor_changes(
    competitor_id: uuid.UUID,
    session: SessionDep,
    scope: Scope,
    min_severity: Severity | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[ChangeResponse]:
    page = await competitor_service.list_changes(
        session,
        scope,
        competitor_id,
        limit=limit,
        offset=offset,
        min_severity_rank=min_severity.rank if min_severity else 0,
    )
    return Paginated(
        items=[ChangeResponse.model_validate(change) for change in page.items],
        meta=page_meta(total=page.total, limit=limit, offset=offset, count=len(page.items)),
    )


def _dispatch(job_id: uuid.UUID) -> None:
    """Hand the job to Celery.

    Import is local so the API can start without a broker configured, and a broker that
    is down produces a queued job the scheduler will pick up rather than a 500 on a
    request that has already been committed.
    """
    from app.core.logging import get_logger

    try:
        from app.workers.tasks import run_analysis_task

        run_analysis_task.delay(str(job_id))
    except Exception as exc:
        get_logger(__name__).warning(
            "job.dispatch_failed", job_id=str(job_id), error=str(exc)[:200]
        )


__all__ = ["router"]
