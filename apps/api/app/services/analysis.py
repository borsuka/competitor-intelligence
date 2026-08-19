"""The analysis pipeline.

Called by a Celery task, never by an HTTP request — a full crawl plus two model calls
takes minutes.  The job row is updated as each stage completes so the UI can show real
progress rather than a spinner.

    crawl → persist snapshots → extract (AI) → positioning (AI) → recommendations (AI)
          → observed SEO → score → embeddings → change detection → alerts

Every stage is written so that a failure leaves the previous stages persisted: an
analysis whose AI call failed still yields fresh snapshots and change detection, which is
most of the value.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import PROMPT_VERSIONS
from app.ai.schemas import ExtractionResult, PositioningResult
from app.ai.service import AIService
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError, QuotaExceededError
from app.core.logging import bind_context, get_logger
from app.core.tenancy import Role, TenantScope
from app.db.base import utcnow
from app.db.models.analysis import Analysis, AnalysisJob, Embedding, PricingPlan, Product, Score
from app.db.models.competitor import Competitor, CompetitorPage, PageSnapshot, SeoSnapshot
from app.db.models.enums import (
    AnalysisDepth,
    BillingPeriod,
    CompetitorStatus,
    DataSource,
    JobStatus,
    JobType,
    PageType,
)
from app.db.models.monitoring import Change
from app.scraping.crawler import CrawlResult, crawl_site
from app.scraping.urls import url_hash
from app.services import alerts, audit, organizations
from app.services import reviews as review_service
from app.services.changes import CompetitorState, PlanState, detect_changes, normalize_name
from app.services.scoring import ScoringInput, compute_score

log = get_logger(__name__)

# Progress checkpoints, so the UI's progress bar corresponds to real work rather than a
# timer.
STAGE_PROGRESS: dict[str, int] = {
    "queued": 0,
    "crawling": 15,
    "storing_snapshots": 40,
    "extracting": 55,
    "analyzing": 70,
    "scoring": 85,
    "detecting_changes": 92,
    "finalizing": 98,
    "done": 100,
}


@dataclass(slots=True)
class AnalysisOutcome:
    analysis_id: uuid.UUID | None
    job_id: uuid.UUID
    status: JobStatus
    changes_detected: int = 0
    pages_crawled: int = 0
    reused_previous: bool = False


# --------------------------------------------------------------------- jobs


async def enqueue_analysis(
    session: AsyncSession,
    scope: TenantScope,
    *,
    competitor_id: uuid.UUID,
    depth: AnalysisDepth = AnalysisDepth.STANDARD,
    job_type: JobType = JobType.FULL_ANALYSIS,
) -> AnalysisJob:
    """Create the job row and hand it to Celery.

    Quota is checked here, before the task is queued: a queued job has already committed
    the organization to crawl and AI spend.
    """
    scope.require(Role.MEMBER)
    await organizations.assert_can_run_analysis(session, scope.organization_id)

    result = await session.execute(
        select(Competitor).where(
            Competitor.id == competitor_id,
            Competitor.organization_id == scope.organization_id,
            Competitor.deleted_at.is_(None),
        )
    )
    competitor = result.scalar_one_or_none()
    if competitor is None:
        raise NotFoundError("Competitor not found.", code="competitor_not_found")

    # One running job per competitor. A second concurrent crawl of the same site would
    # double the cost and race on the "current" product/pricing rows.
    running = await session.execute(
        select(AnalysisJob).where(
            AnalysisJob.competitor_id == competitor_id,
            AnalysisJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
    )
    existing = running.scalar_one_or_none()
    if existing is not None:
        return existing

    job = AnalysisJob(
        organization_id=scope.organization_id,
        competitor_id=competitor_id,
        triggered_by_user_id=scope.user_id,
        job_type=job_type,
        status=JobStatus.PENDING,
        depth=depth,
        params={"website_url": competitor.website_url},
    )
    session.add(job)
    await session.flush()

    await audit.record(
        session,
        action="analysis.enqueued",
        organization_id=scope.organization_id,
        actor_user_id=scope.user_id,
        resource_type="competitor",
        resource_id=competitor_id,
        metadata={"job_id": str(job.id), "depth": depth.value},
    )
    return job


async def get_job(session: AsyncSession, scope: TenantScope, job_id: uuid.UUID) -> AnalysisJob:
    result = await session.execute(
        select(AnalysisJob).where(
            AnalysisJob.id == job_id, AnalysisJob.organization_id == scope.organization_id
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job not found.", code="job_not_found")
    return job


async def cancel_job(session: AsyncSession, scope: TenantScope, job_id: uuid.UUID) -> AnalysisJob:
    scope.require(Role.MEMBER)
    job = await get_job(session, scope, job_id)
    if job.status.is_terminal:
        return job
    job.status = JobStatus.CANCELLED
    job.finished_at = utcnow()
    return job


async def _set_stage(session: AsyncSession, job: AnalysisJob, stage: str) -> None:
    job.stage = stage
    job.progress = STAGE_PROGRESS.get(stage, job.progress)
    await session.flush()
    log.info("analysis.stage", job_id=str(job.id), stage=stage, progress=job.progress)


# ----------------------------------------------------------------- pipeline


async def run_analysis(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    ai: AIService | None = None,
) -> AnalysisOutcome:
    """Execute one analysis job end to end.

    Owns the transaction: commits at meaningful checkpoints so a later failure does not
    discard work that already succeeded.
    """
    job = await session.get(AnalysisJob, job_id)
    if job is None:
        raise NotFoundError("Job not found.", code="job_not_found")
    if job.status is JobStatus.CANCELLED:
        return AnalysisOutcome(analysis_id=None, job_id=job_id, status=JobStatus.CANCELLED)

    competitor = await session.get(Competitor, job.competitor_id)
    if competitor is None or competitor.deleted_at is not None:
        job.status = JobStatus.FAILED
        job.error_code = "competitor_not_found"
        job.error_message = "The competitor was deleted before the analysis ran."
        job.finished_at = utcnow()
        await session.commit()
        return AnalysisOutcome(analysis_id=None, job_id=job_id, status=JobStatus.FAILED)

    bind_context(
        job_id=str(job.id),
        organization_id=str(job.organization_id),
        competitor_id=str(competitor.id),
    )

    ai = ai or AIService()
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.started_at = utcnow()
    await _set_stage(session, job, "crawling")
    await session.commit()

    try:
        outcome = await _execute(session, job=job, competitor=competitor, ai=ai)
    except QuotaExceededError as exc:
        await _fail(session, job, exc.code, exc.message)
        raise
    except AppError as exc:
        await _fail(session, job, exc.code, exc.message)
        raise
    except Exception as exc:
        log.exception("analysis.unexpected_failure", job_id=str(job.id))
        await _fail(session, job, "internal_error", str(exc)[:500])
        raise

    return outcome


async def _fail(session: AsyncSession, job: AnalysisJob, code: str, message: str) -> None:
    job.status = JobStatus.FAILED
    job.error_code = code
    job.error_message = message[:2000]
    job.finished_at = utcnow()
    await session.commit()
    log.warning("analysis.failed", job_id=str(job.id), error_code=code)


async def _execute(
    session: AsyncSession,
    *,
    job: AnalysisJob,
    competitor: Competitor,
    ai: AIService,
) -> AnalysisOutcome:
    depth = job.depth
    max_pages = {
        AnalysisDepth.QUICK: 8,
        AnalysisDepth.STANDARD: 25,
        AnalysisDepth.DEEP: 50,
    }.get(depth, 25)

    # ---------------------------------------------------------------- crawl
    crawl = await crawl_site(competitor.website_url, max_pages=max_pages)
    if not crawl.succeeded:
        first_error = crawl.errors[0] if crawl.errors else None
        await _fail(
            session,
            job,
            first_error.code if first_error else "crawl_failed",
            first_error.message if first_error else "No pages could be fetched.",
        )
        return AnalysisOutcome(analysis_id=None, job_id=job.id, status=JobStatus.FAILED)

    # The state we will diff against, captured before the new snapshots land.
    previous_state = await _load_state(session, competitor.id)
    # Whether there is anything to compare against at all. On a first analysis every
    # product, plan and page would register as "added", which is noise, not intelligence.
    had_previous_analysis = await _has_previous_analysis(session, competitor.id)

    # No review source is configured by default, so this returns an empty batch and the
    # sentiment dimension stays "insufficient data" rather than being invented.
    review_provider = review_service.build_review_provider()
    try:
        review_batch = await review_provider.fetch(
            domain=competitor.domain, company_name=competitor.name
        )
    except Exception as exc:  # a review source must never fail an analysis
        log.warning("analysis.reviews_unavailable", error=str(exc)[:200])
        review_batch = review_service.ReviewBatch()
    finally:
        await review_provider.aclose()

    await _set_stage(session, job, "storing_snapshots")
    await _store_snapshots(session, competitor=competitor, crawl=crawl)
    await organizations.record_usage(session, competitor.organization_id, pages=len(crawl.pages))
    await session.commit()

    # ------------------------------------------------------------------ AI
    fingerprint = _content_fingerprint(crawl)
    reused = await _find_reusable_analysis(session, competitor.id, fingerprint)
    if reused is not None and job.job_type is JobType.REFRESH:
        # Nothing changed since the last analysis, so re-running the model would buy an
        # identical answer at full price.
        log.info("analysis.reused_previous", analysis_id=str(reused.id))
        await _finalize(session, job=job, competitor=competitor, analysis=reused, changes=0)
        return AnalysisOutcome(
            analysis_id=reused.id,
            job_id=job.id,
            status=JobStatus.COMPLETED,
            pages_crawled=len(crawl.pages),
            reused_previous=True,
        )

    pages_payload = _pages_payload(crawl)
    observed_prices = _observed_prices(crawl)

    await _set_stage(session, job, "extracting")
    extraction, pricing_notes = await ai.extract(
        company_name=competitor.name,
        pages=pages_payload,
        observed_prices=observed_prices,
        depth=depth,
    )

    await _set_stage(session, job, "analyzing")
    positioning = await ai.analyze_positioning(
        company_name=competitor.name,
        domain=competitor.domain,
        pages=pages_payload,
        extracted=extraction.data,
        external_links=_external_links(crawl),
        depth=depth,
    )

    from app.db.models.identity import Organization

    organization = await session.get(Organization, competitor.organization_id)
    recommendation_result = await ai.recommend(
        own_company_name=organization.own_company_name if organization else None,
        own_company_description=(organization.own_company_description if organization else None),
        competitor_name=competitor.name,
        analysis_summary=_analysis_summary(extraction.data, positioning.data),
    )

    injection_flags = _collect_injection_flags(crawl)
    analysis = Analysis(
        organization_id=competitor.organization_id,
        competitor_id=competitor.id,
        job_id=job.id,
        provider=extraction.provider,
        model=positioning.model,
        is_mock=extraction.is_mock or positioning.is_mock,
        prompt_versions=dict(PROMPT_VERSIONS),
        depth=depth,
        summary=positioning.data.company_summary,
        positioning=positioning.data.positioning_statement or None,
        target_audience=positioning.data.target_audience,
        value_propositions=positioning.data.value_propositions,
        strengths=[insight.model_dump() for insight in positioning.data.strengths],
        weaknesses=[insight.model_dump() for insight in positioning.data.weaknesses],
        marketing_channels=positioning.data.marketing_channels,
        recommendations=(
            [rec.model_dump() for rec in recommendation_result.data.recommendations]
            if recommendation_result
            else []
        ),
        key_features=extraction.data.key_features,
        confidence=min(extraction.data.confidence, positioning.data.confidence),
        pages_analyzed=len(crawl.pages),
        content_fingerprint=fingerprint,
        injection_flags=injection_flags,
        tokens_in=extraction.tokens_in + positioning.tokens_in,
        tokens_out=extraction.tokens_out + positioning.tokens_out,
        duration_ms=extraction.duration_ms + positioning.duration_ms,
        # User-visible: why a plan shows no price, and what became of any prices that
        # were found but could not be matched to a plan.
        data_notes=(
            [*pricing_notes, extraction.data.pricing_model_notes]
            if extraction.data.pricing_model_notes
            else pricing_notes
        ),
    )
    session.add(analysis)
    await session.flush()

    await _store_products(
        session, competitor=competitor, analysis=analysis, extraction=extraction.data
    )
    await _store_pricing(
        session, competitor=competitor, analysis=analysis, extraction=extraction.data
    )
    await organizations.record_usage(
        session,
        competitor.organization_id,
        analyses=1,
        tokens_in=analysis.tokens_in,
        tokens_out=analysis.tokens_out,
    )
    await session.commit()

    # -------------------------------------------------------------- scoring
    await _set_stage(session, job, "scoring")
    seo = await _store_seo_snapshot(session, competitor=competitor, crawl=crawl)
    score = await _store_score(
        session,
        competitor=competitor,
        analysis=analysis,
        extraction=extraction.data,
        positioning=positioning.data,
        crawl=crawl,
        seo=seo,
        reviews=review_batch,
    )
    await _store_embeddings(session, competitor=competitor, crawl=crawl, ai=ai)
    await session.commit()

    # ------------------------------------------------------ change detection
    await _set_stage(session, job, "detecting_changes")
    if had_previous_analysis:
        current_state = await _load_state(session, competitor.id)
        detected = detect_changes(previous_state, current_state, competitor_name=competitor.name)
    else:
        log.info("analysis.baseline_established", competitor_id=str(competitor.id))
        detected = []

    change_rows: list[Change] = []
    now = utcnow()
    for change in detected:
        change_rows.append(
            Change(
                created_at=now,
                organization_id=competitor.organization_id,
                competitor_id=competitor.id,
                analysis_id=analysis.id,
                change_type=change.change_type,
                severity=change.severity,
                title=change.title[:300],
                description=change.description,
                entity_key=(change.entity_key or "")[:200] or None,
                before=change.before,
                after=change.after,
                magnitude=change.magnitude,
                source_url=change.source_url,
                detected_at=now,
            )
        )
    session.add_all(change_rows)
    await session.flush()

    if change_rows:
        await alerts.dispatch_for_changes(session, competitor=competitor, changes=change_rows)

    await _set_stage(session, job, "finalizing")
    await _finalize(
        session,
        job=job,
        competitor=competitor,
        analysis=analysis,
        changes=len(change_rows),
        score=score,
    )

    return AnalysisOutcome(
        analysis_id=analysis.id,
        job_id=job.id,
        status=JobStatus.COMPLETED,
        changes_detected=len(change_rows),
        pages_crawled=len(crawl.pages),
    )


async def _finalize(
    session: AsyncSession,
    *,
    job: AnalysisJob,
    competitor: Competitor,
    analysis: Analysis,
    changes: int,
    score: Score | None = None,
) -> None:
    job.status = JobStatus.COMPLETED
    job.progress = 100
    job.stage = "done"
    job.finished_at = utcnow()

    competitor.last_analyzed_at = utcnow()
    competitor.latest_analysis_id = analysis.id
    if score is not None:
        competitor.latest_overall_score = score.overall
    if competitor.monitoring_enabled:
        competitor.next_monitor_at = utcnow() + timedelta(
            hours=competitor.monitoring_interval_hours
        )

    await session.commit()
    log.info(
        "analysis.completed",
        job_id=str(job.id),
        analysis_id=str(analysis.id),
        changes=changes,
        is_mock=analysis.is_mock,
    )


# ------------------------------------------------------------- persistence


async def _store_snapshots(
    session: AsyncSession, *, competitor: Competitor, crawl: CrawlResult
) -> None:
    """Upsert pages and append one snapshot each.

    Pages are loaded in one query and matched in memory rather than queried per URL —
    twenty-five round trips per crawl adds up quickly across a scheduled sweep.
    """
    existing_result = await session.execute(
        select(CompetitorPage).where(CompetitorPage.competitor_id == competitor.id)
    )
    pages_by_hash = {page.url_hash: page for page in existing_result.scalars().all()}
    seen_hashes: set[str] = set()

    for crawled in crawl.pages:
        digest = url_hash(crawled.url)
        seen_hashes.add(digest)
        page = pages_by_hash.get(digest)
        if page is None:
            page = CompetitorPage(
                organization_id=competitor.organization_id,
                competitor_id=competitor.id,
                url=crawled.url,
                url_hash=digest,
                page_type=crawled.page_type,
                title=crawled.extracted.title,
                discovery_score=crawled.discovery_score,
            )
            session.add(page)
            await session.flush()
            pages_by_hash[digest] = page
        else:
            page.title = crawled.extracted.title
            page.page_type = crawled.page_type
            page.is_active = True

        page.last_status_code = crawled.status_code
        page.last_fetched_at = crawled.fetched_at

        extracted = crawled.extracted
        session.add(
            PageSnapshot(
                organization_id=competitor.organization_id,
                competitor_id=competitor.id,
                page_id=page.id,
                fetched_at=crawled.fetched_at,
                http_status=crawled.status_code,
                content_hash=extracted.content_hash,
                text_hash=extracted.text_hash,
                title=extracted.title,
                meta_description=extracted.meta_description,
                canonical_url=extracted.canonical_url,
                lang=extracted.lang,
                headings=extracted.headings,
                structured_data=extracted.structured_data,
                open_graph=extracted.open_graph,
                links={
                    "internal": [link["url"] for link in extracted.internal_links[:200]],
                    "external": extracted.external_links[:100],
                },
                detected_prices=extracted.detected_prices,
                calls_to_action=extracted.calls_to_action,
                text_content=extracted.text_content[:200_000],
                word_count=extracted.word_count,
                render_mode=crawled.render_mode,
                fetch_duration_ms=crawled.duration_ms,
            )
        )

    # Pages that vanished stay in the table (their history is still interesting) but are
    # deactivated so they are not counted as current.
    stale = [digest for digest in pages_by_hash if digest not in seen_hashes]
    if stale:
        await session.execute(
            update(CompetitorPage)
            .where(
                CompetitorPage.competitor_id == competitor.id,
                CompetitorPage.url_hash.in_(stale),
            )
            .values(is_active=False)
        )


async def _store_products(
    session: AsyncSession,
    *,
    competitor: Competitor,
    analysis: Analysis,
    extraction: ExtractionResult,
) -> None:
    result = await session.execute(select(Product).where(Product.competitor_id == competitor.id))
    existing = {product.normalized_name: product for product in result.scalars().all()}
    now = utcnow()
    seen: set[str] = set()

    for product in extraction.products:
        key = normalize_name(product.name)[:200]
        if not key or key in seen:
            continue
        seen.add(key)
        row = existing.get(key)
        if row is None:
            session.add(
                Product(
                    organization_id=competitor.organization_id,
                    competitor_id=competitor.id,
                    analysis_id=analysis.id,
                    name=product.name[:200],
                    normalized_name=key,
                    description=product.description,
                    category=product.category,
                    features=product.features,
                    source_url=product.source_url,
                    source=DataSource.AI_INFERENCE,
                    is_current=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        else:
            row.name = product.name[:200]
            row.description = product.description
            row.features = product.features
            row.source_url = product.source_url
            row.analysis_id = analysis.id
            row.is_current = True
            row.last_seen_at = now

    for key, row in existing.items():
        if key not in seen:
            row.is_current = False


async def _store_pricing(
    session: AsyncSession,
    *,
    competitor: Competitor,
    analysis: Analysis,
    extraction: ExtractionResult,
) -> None:
    result = await session.execute(
        select(PricingPlan).where(PricingPlan.competitor_id == competitor.id)
    )
    existing = {
        (plan.normalized_name, plan.billing_period): plan for plan in result.scalars().all()
    }
    now = utcnow()
    seen: set[tuple[str, BillingPeriod]] = set()

    for plan in extraction.pricing_plans:
        key_name = normalize_name(plan.name)[:120]
        if not key_name:
            continue
        key = (key_name, plan.billing_period)
        if key in seen:
            continue
        seen.add(key)

        amount = Decimal(str(plan.amount)) if plan.amount is not None else None
        row = existing.get(key)
        if row is None:
            session.add(
                PricingPlan(
                    organization_id=competitor.organization_id,
                    competitor_id=competitor.id,
                    analysis_id=analysis.id,
                    name=plan.name[:120],
                    normalized_name=key_name,
                    amount=amount,
                    currency=plan.currency,
                    billing_period=plan.billing_period,
                    is_custom_pricing=plan.is_custom_pricing,
                    is_free=plan.is_free,
                    features=plan.features,
                    highlights=plan.highlights,
                    source_url=plan.source_url,
                    # Amounts survived enforce_observed_prices, so they were seen on a page.
                    source=DataSource.OBSERVED if amount is not None else DataSource.AI_INFERENCE,
                    is_current=True,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        else:
            row.name = plan.name[:120]
            row.amount = amount
            row.currency = plan.currency
            row.is_custom_pricing = plan.is_custom_pricing
            row.is_free = plan.is_free
            row.features = plan.features
            row.source_url = plan.source_url
            row.analysis_id = analysis.id
            row.is_current = True
            row.last_seen_at = now

    for key, row in existing.items():
        if key not in seen:
            row.is_current = False


async def _store_seo_snapshot(
    session: AsyncSession, *, competitor: Competitor, crawl: CrawlResult
) -> SeoSnapshot:
    pages = crawl.pages
    structured_types: set[str] = set()
    for page in pages:
        for block in page.extracted.structured_data:
            value = block.get("@type")
            if isinstance(value, str):
                structured_types.add(value)
            elif isinstance(value, list):
                structured_types.update(str(item) for item in value[:5])

    snapshot = SeoSnapshot(
        organization_id=competitor.organization_id,
        competitor_id=competitor.id,
        captured_at=utcnow(),
        pages_crawled=len(pages),
        pages_discovered=len(crawl.discovered_urls),
        has_sitemap=crawl.has_sitemap,
        sitemap_url_count=crawl.sitemap_url_count,
        has_robots_txt=crawl.has_robots_txt,
        has_blog=any(page.page_type is PageType.BLOG for page in pages),
        pages_missing_title=sum(1 for page in pages if not page.extracted.title),
        pages_missing_meta_description=sum(
            1 for page in pages if not page.extracted.meta_description
        ),
        pages_missing_h1=sum(1 for page in pages if not page.extracted.has_h1),
        avg_title_length=(
            sum(len(page.extracted.title or "") for page in pages) / len(pages) if pages else None
        ),
        avg_word_count=(
            sum(page.extracted.word_count for page in pages) / len(pages) if pages else None
        ),
        internal_link_count=sum(len(page.extracted.internal_links) for page in pages),
        external_link_count=len(_external_links(crawl)),
        structured_data_types=sorted(structured_types)[:20],
        content_topics=[],
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def _store_score(
    session: AsyncSession,
    *,
    competitor: Competitor,
    analysis: Analysis,
    extraction: ExtractionResult,
    positioning: PositioningResult,
    crawl: CrawlResult,
    seo: SeoSnapshot,
    reviews: review_service.ReviewBatch,
) -> Score:
    products = extraction.products
    plans = extraction.pricing_plans

    scoring_input = ScoringInput(
        product_count=len(products),
        products_with_description=sum(1 for product in products if product.description),
        feature_count=len(extraction.key_features),
        features_per_product=(
            sum(len(product.features) for product in products) / len(products) if products else 0.0
        ),
        pricing_plan_count=len(plans),
        plans_with_public_amount=sum(1 for plan in plans if plan.amount is not None),
        has_free_tier=any(plan.is_free for plan in plans),
        has_custom_pricing=any(plan.is_custom_pricing for plan in plans),
        pricing_page_found=any(page.page_type is PageType.PRICING for page in crawl.pages),
        value_proposition_count=len(positioning.value_propositions),
        has_positioning_statement=bool(positioning.positioning_statement),
        target_audience_count=len(positioning.target_audience),
        ai_confidence=positioning.confidence,
        pages_crawled=seo.pages_crawled,
        pages_missing_title=seo.pages_missing_title,
        pages_missing_meta_description=seo.pages_missing_meta_description,
        pages_missing_h1=seo.pages_missing_h1,
        has_sitemap=seo.has_sitemap,
        has_robots_txt=seo.has_robots_txt,
        has_blog=seo.has_blog,
        structured_data_type_count=len(seo.structured_data_types),
        avg_word_count=float(seo.avg_word_count or 0.0),
        internal_link_count=seo.internal_link_count,
        marketing_channel_count=len(positioning.marketing_channels),
        social_profile_count=_social_profile_count(crawl),
        case_study_pages=sum(1 for page in crawl.pages if page.page_type is PageType.CASE_STUDY),
        cta_count=len({cta for page in crawl.pages for cta in page.extracted.calls_to_action}),
        open_graph_tag_count=max(
            (len(page.extracted.open_graph) for page in crawl.pages), default=0
        ),
        has_favicon=bool(competitor.favicon_url),
        external_link_count=len(_external_links(crawl)),
        review_count=reviews.count,
        sentiment_score=reviews.sentiment_score,
    )

    result = compute_score(scoring_input)
    score = Score(
        organization_id=competitor.organization_id,
        competitor_id=competitor.id,
        analysis_id=analysis.id,
        overall=result.overall,
        dimensions=result.dimensions,
        threat_level=result.threat_level,
        confidence=result.confidence,
        data_completeness=result.data_completeness,
        methodology_version=result.methodology_version,
    )
    session.add(score)
    analysis.data_completeness = result.data_completeness
    await session.flush()
    return score


async def _store_embeddings(
    session: AsyncSession, *, competitor: Competitor, crawl: CrawlResult, ai: AIService
) -> None:
    """Embed page content for semantic search.

    Content-hash keyed, so re-analysing an unchanged site does not re-embed (and, with a
    paid embedding provider, does not re-pay).
    """
    from app.ai.providers.embeddings import chunk_text

    existing_result = await session.execute(
        select(Embedding.content_hash).where(Embedding.competitor_id == competitor.id)
    )
    known = set(existing_result.scalars().all())

    pending: list[tuple[str, str, str, int]] = []  # (hash, content, url, index)
    for page in crawl.pages:
        for index, chunk in enumerate(chunk_text(page.extracted.text_content)):
            digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            if digest in known:
                continue
            known.add(digest)
            pending.append((digest, chunk, page.url, index))

    if not pending:
        return

    pending = pending[:200]  # a hard ceiling on per-analysis embedding spend
    embedding_result = await ai.embed([chunk for _, chunk, _, _ in pending])
    now = utcnow()

    for (digest, chunk, url, index), vector in zip(pending, embedding_result.vectors, strict=False):
        session.add(
            Embedding(
                created_at=now,
                organization_id=competitor.organization_id,
                competitor_id=competitor.id,
                source_type="page",
                source_url=url,
                chunk_index=index,
                content=chunk,
                content_hash=digest,
                model=embedding_result.model,
                embedding=vector,
            )
        )


# ---------------------------------------------------------------- helpers


async def _load_state(session: AsyncSession, competitor_id: uuid.UUID) -> CompetitorState:
    """Read the current comparable state of a competitor for diffing."""
    plans_result = await session.execute(
        select(PricingPlan).where(
            PricingPlan.competitor_id == competitor_id, PricingPlan.is_current.is_(True)
        )
    )
    products_result = await session.execute(
        select(Product.name).where(
            Product.competitor_id == competitor_id, Product.is_current.is_(True)
        )
    )
    analysis_result = await session.execute(
        select(Analysis)
        .where(Analysis.competitor_id == competitor_id)
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    latest = analysis_result.scalar_one_or_none()

    snapshots_result = await session.execute(
        select(PageSnapshot)
        .where(PageSnapshot.competitor_id == competitor_id)
        .distinct(PageSnapshot.page_id)
        .order_by(PageSnapshot.page_id, PageSnapshot.fetched_at.desc())
    )
    snapshots = list(snapshots_result.scalars().all())

    return CompetitorState(
        plans=[
            PlanState(
                name=plan.name,
                amount=float(plan.amount) if plan.amount is not None else None,
                currency=plan.currency,
                billing_period=plan.billing_period.value,
                is_custom_pricing=plan.is_custom_pricing,
                source_url=plan.source_url,
            )
            for plan in plans_result.scalars().all()
        ],
        products=list(products_result.scalars().all()),
        features=list(latest.key_features) if latest else [],
        positioning=(latest.summary if latest else None),
        page_hashes={str(snapshot.page_id): snapshot.text_hash for snapshot in snapshots},
        page_titles={str(snapshot.page_id): snapshot.title or "" for snapshot in snapshots},
    )


def _content_fingerprint(crawl: CrawlResult) -> str:
    """Hash of the crawled text.

    Identical fingerprint means identical input, which means an AI re-run would produce
    the same answer — so it is skipped.
    """
    digest = hashlib.sha256()
    for page in sorted(crawl.pages, key=lambda page: page.url):
        digest.update(page.url.encode("utf-8"))
        digest.update(page.extracted.text_hash.encode("utf-8"))
    return digest.hexdigest()


async def _has_previous_analysis(session: AsyncSession, competitor_id: uuid.UUID) -> bool:
    """True when this competitor has been analysed before.

    The first run establishes the baseline; only the second and later runs can produce
    changes.
    """
    result = await session.execute(
        select(Analysis.id).where(Analysis.competitor_id == competitor_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _find_reusable_analysis(
    session: AsyncSession, competitor_id: uuid.UUID, fingerprint: str
) -> Analysis | None:
    result = await session.execute(
        select(Analysis)
        .where(
            Analysis.competitor_id == competitor_id,
            Analysis.content_fingerprint == fingerprint,
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _pages_payload(crawl: CrawlResult) -> list[dict[str, Any]]:
    """Order pages by analytical value before the token budget truncates the list."""
    ordered = sorted(
        crawl.pages,
        key=lambda page: (
            0 if page.page_type is PageType.HOME else 1,
            -page.discovery_score,
        ),
    )
    return [
        {
            "url": page.url,
            "page_type": page.page_type.value,
            "title": page.extracted.title,
            "meta_description": page.extracted.meta_description,
            "text": page.extracted.text_content,
            "headings": [
                heading for values in page.extracted.headings.values() for heading in values
            ],
            "calls_to_action": page.extracted.calls_to_action,
            "word_count": page.extracted.word_count,
        }
        for page in ordered
    ]


def _observed_prices(crawl: CrawlResult) -> list[dict[str, Any]]:
    """Prices the parser found, tagged with the kind of page they came from.

    When a pricing page was crawled, only its prices are offered. Everything else is
    noise dressed as signal: a shop's homepage is covered in product prices, an about
    page mentions "$1", and a changelog quotes old figures. Feeding those to the
    extraction stage produces a table of plans a competitor does not have.
    """
    by_page: list[dict[str, Any]] = []
    for page in crawl.pages:
        for price in page.extracted.detected_prices:
            by_page.append({**price, "source_url": page.url, "page_type": page.page_type.value})

    from_pricing_pages = [
        price for price in by_page if price["page_type"] == PageType.PRICING.value
    ]
    if from_pricing_pages:
        return from_pricing_pages[:60]

    # No pricing page. The prices are still reported so the model can see them and say
    # what they are, but nothing here should be read as a pricing tier.
    return by_page[:60]


def _external_links(crawl: CrawlResult) -> list[str]:
    links: set[str] = set()
    for page in crawl.pages:
        links.update(page.extracted.external_links)
    return sorted(links)


_SOCIAL_HOSTS = (
    "twitter.com",
    "x.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "github.com",
    "tiktok.com",
    "reddit.com",
    "discord.gg",
    "medium.com",
)


def _social_profile_count(crawl: CrawlResult) -> int:
    hosts = {host for link in _external_links(crawl) for host in _SOCIAL_HOSTS if host in link}
    return len(hosts)


def _collect_injection_flags(crawl: CrawlResult) -> list[dict[str, Any]]:
    """Record which pages contained instruction-like text.

    Stored on the analysis and shown in the UI: if a competitor's site is trying to talk
    to the model, the user should know that is why the output reads oddly.
    """
    from app.ai.sanitize import scan_for_injection

    flags: list[dict[str, Any]] = []
    for page in crawl.pages:
        detected = scan_for_injection(page.extracted.text_content)
        if detected:
            flags.append({"url": page.url, "patterns": list(detected)})
    return flags[:20]


def _analysis_summary(extraction: ExtractionResult, positioning: PositioningResult) -> str:
    plans = "; ".join(
        f"{plan.name}: "
        + (
            "custom pricing"
            if plan.is_custom_pricing
            else f"{plan.currency or ''} {plan.amount}".strip()
            if plan.amount is not None
            else "price not published"
        )
        for plan in extraction.pricing_plans[:8]
    )
    return (
        f"Summary: {positioning.company_summary}\n"
        f"Positioning: {positioning.positioning_statement or 'not stated'}\n"
        f"Target audience: {', '.join(positioning.target_audience) or 'unknown'}\n"
        f"Products: {', '.join(product.name for product in extraction.products[:10]) or 'none'}\n"
        f"Pricing: {plans or 'none extracted'}\n"
        f"Strengths: {'; '.join(insight.title for insight in positioning.strengths) or 'none'}\n"
        f"Weaknesses: {'; '.join(insight.title for insight in positioning.weaknesses) or 'none'}"
    )


async def refresh_due_competitors(session: AsyncSession) -> list[uuid.UUID]:
    """Scheduler entry point: enqueue refreshes for competitors whose interval elapsed."""
    from app.services.competitors import due_for_monitoring

    due = await due_for_monitoring(session)
    job_ids: list[uuid.UUID] = []
    now = utcnow()

    for competitor in due:
        if competitor.status is not CompetitorStatus.ACTIVE:
            continue
        running = await session.execute(
            select(AnalysisJob.id).where(
                AnalysisJob.competitor_id == competitor.id,
                AnalysisJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
        if running.scalar_one_or_none() is not None:
            continue

        job = AnalysisJob(
            organization_id=competitor.organization_id,
            competitor_id=competitor.id,
            job_type=JobType.REFRESH,
            status=JobStatus.PENDING,
            depth=AnalysisDepth.STANDARD,
            params={"website_url": competitor.website_url, "scheduled": True},
        )
        session.add(job)
        # Move the next check forward immediately so a slow run is not re-enqueued on the
        # scheduler's next tick.
        competitor.next_monitor_at = now + timedelta(hours=competitor.monitoring_interval_hours)
        await session.flush()
        job_ids.append(job.id)

    await session.commit()
    return job_ids


__all__ = [
    "STAGE_PROGRESS",
    "AnalysisOutcome",
    "cancel_job",
    "enqueue_analysis",
    "get_job",
    "refresh_due_competitors",
    "run_analysis",
]


async def prune_snapshot_text(session: AsyncSession, *, older_than_days: int | None = None) -> int:
    """Clear page text from snapshots past the retention window.

    The rows stay. Change detection compares ``text_hash``, which is kept, so competitor
    history and every previously detected diff remain intact — what goes is the stored
    page body, which is the overwhelming majority of the bytes and is only needed while a
    snapshot is recent enough to re-analyse.

    Returns the number of snapshots cleared.
    """
    settings = get_settings()
    days = older_than_days if older_than_days is not None else settings.snapshot_text_retention_days
    if days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=days)
    result = await session.execute(
        update(PageSnapshot)
        .where(PageSnapshot.fetched_at < cutoff, PageSnapshot.text_content != "")
        .values(text_content="")
    )
    cleared = int(result.rowcount or 0)
    if cleared:
        log.info("analysis.snapshot_text_pruned", cleared=cleared, older_than_days=days)
    return cleared
