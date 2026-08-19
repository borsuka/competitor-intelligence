"""Dashboard, comparisons, changes, reports, search and job status."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import MemberScope, Scope, SessionDep
from app.db.models.enums import Severity
from app.schemas.common import Paginated, page_meta
from app.schemas.competitor import (
    ChangeResponse,
    ComparisonCreate,
    ComparisonResponse,
    DashboardResponse,
    JobResponse,
    ReportCreate,
    ReportResponse,
    SearchHitResponse,
    SearchResponse,
)
from app.services import alerts as alert_service
from app.services import analysis as analysis_service
from app.services import comparison as comparison_service
from app.services import competitors as competitor_service
from app.services import dashboard as dashboard_service
from app.services import reports as report_service
from app.services import search as search_service

router = APIRouter(prefix="/orgs/{organization_id}", tags=["insights"])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(session: SessionDep, scope: Scope) -> DashboardResponse:
    overview = await dashboard_service.overview(session, scope)
    return DashboardResponse(**asdict(overview))


# ---------------------------------------------------------------- comparisons


@router.post("/comparisons", response_model=ComparisonResponse, status_code=status.HTTP_201_CREATED)
async def create_comparison(
    payload: ComparisonCreate, session: SessionDep, scope: MemberScope
) -> ComparisonResponse:
    comparison = await comparison_service.create_comparison(
        session,
        scope,
        competitor_ids=payload.competitor_ids,
        name=payload.name,
        with_insights=payload.with_insights,
    )
    await session.commit()
    return ComparisonResponse.model_validate(comparison)


@router.get("/comparisons", response_model=list[ComparisonResponse])
async def list_comparisons(
    session: SessionDep,
    scope: Scope,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ComparisonResponse]:
    comparisons = await comparison_service.list_comparisons(session, scope, limit=limit)
    return [ComparisonResponse.model_validate(item) for item in comparisons]


@router.get("/comparisons/{comparison_id}", response_model=ComparisonResponse)
async def get_comparison(
    comparison_id: uuid.UUID, session: SessionDep, scope: Scope
) -> ComparisonResponse:
    comparison = await comparison_service.get_comparison(session, scope, comparison_id)
    return ComparisonResponse.model_validate(comparison)


# -------------------------------------------------------------------- changes


@router.get("/changes", response_model=Paginated[ChangeResponse])
async def list_changes(
    session: SessionDep,
    scope: Scope,
    min_severity: Severity | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[ChangeResponse]:
    page = await competitor_service.list_changes(
        session,
        scope,
        None,
        limit=limit,
        offset=offset,
        min_severity_rank=min_severity.rank if min_severity else 0,
    )
    return Paginated(
        items=[ChangeResponse.model_validate(change) for change in page.items],
        meta=page_meta(total=page.total, limit=limit, offset=offset, count=len(page.items)),
    )


@router.post("/changes/{change_id}/acknowledge", response_model=ChangeResponse)
async def acknowledge_change(
    change_id: uuid.UUID, session: SessionDep, scope: MemberScope
) -> ChangeResponse:
    change = await alert_service.acknowledge_change(session, scope, change_id)
    await session.commit()
    return ChangeResponse.model_validate(change)


# --------------------------------------------------------------------- jobs


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, session: SessionDep, scope: Scope) -> JobResponse:
    """Polled by the UI while an analysis runs."""
    job = await analysis_service.get_job(session, scope, job_id)
    return JobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: uuid.UUID, session: SessionDep, scope: MemberScope) -> JobResponse:
    job = await analysis_service.cancel_job(session, scope, job_id)
    await session.commit()
    return JobResponse.model_validate(job)


# ------------------------------------------------------------------- reports


@router.post("/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(payload: ReportCreate, session: SessionDep, scope: Scope) -> ReportResponse:
    report = await report_service.generate(
        session,
        scope,
        report_type=payload.report_type,
        competitor_ids=payload.competitor_ids,
        period_days=payload.period_days,
        title=payload.title,
    )
    await session.commit()
    return ReportResponse.model_validate(report)


@router.get("/reports", response_model=list[ReportResponse])
async def list_reports(
    session: SessionDep, scope: Scope, limit: Annotated[int, Query(ge=1, le=50)] = 25
) -> list[ReportResponse]:
    reports = await report_service.list_reports(session, scope, limit=limit)
    return [ReportResponse.model_validate(report) for report in reports]


@router.get("/reports/{report_id}", response_model=ReportResponse)
async def get_report(report_id: uuid.UUID, session: SessionDep, scope: Scope) -> ReportResponse:
    report = await report_service.get_report(session, scope, report_id)
    return ReportResponse.model_validate(report)


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(report_id: uuid.UUID, session: SessionDep, scope: MemberScope) -> None:
    await report_service.delete_report(session, scope, report_id)
    await session.commit()


# -------------------------------------------------------------------- search


@router.get("/search", response_model=SearchResponse)
async def search(
    session: SessionDep,
    scope: Scope,
    q: Annotated[str, Query(min_length=2, max_length=500)],
    competitor_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    result = await search_service.semantic_search(
        session, scope, query=q, competitor_id=competitor_id, limit=limit
    )
    return SearchResponse(
        query=result.query,
        hits=[SearchHitResponse(**asdict(hit)) for hit in result.hits],
        provider=result.provider,
        is_lexical=result.is_lexical,
    )


__all__ = ["router"]
