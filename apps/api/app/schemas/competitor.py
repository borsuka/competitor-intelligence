"""Competitor, analysis, scoring and monitoring contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator

from app.core.tenancy import Role
from app.db.models.enums import (
    AnalysisDepth,
    BillingPeriod,
    ChangeType,
    CompetitorStatus,
    DataSource,
    Importance,
    JobStatus,
    JobType,
    NotificationChannel,
    OrgPlan,
    PageType,
    ReportType,
    Severity,
)
from app.schemas.common import APIModel

# ------------------------------------------------------------------ organization


class OrganizationResponse(APIModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: OrgPlan
    own_company_name: str | None
    own_company_url: str | None
    own_company_description: str | None
    created_at: datetime


class OrganizationUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    own_company_name: str | None = Field(default=None, max_length=120)
    own_company_url: str | None = Field(default=None, max_length=2048)
    own_company_description: str | None = Field(default=None, max_length=2000)


class MemberResponse(APIModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    joined_at: datetime


class InviteRequest(APIModel):
    email: str = Field(max_length=320)
    role: Role = Role.MEMBER


class InviteResponse(APIModel):
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    # Development affordance, as with email verification: omitted in production.
    invite_token: str | None = None


class RoleUpdate(APIModel):
    role: Role


class UsageResponse(APIModel):
    period: str
    analyses_used: int
    analyses_limit: int
    pages_crawled: int
    pages_limit: int
    competitors_used: int
    competitors_limit: int
    ai_tokens_in: int
    ai_tokens_out: int


# -------------------------------------------------------------------- competitor


class CompetitorCreate(APIModel):
    website_url: str = Field(min_length=3, max_length=2048)
    name: str | None = Field(default=None, max_length=160)
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=12)
    notes: str | None = Field(default=None, max_length=5000)
    importance: Importance = Importance.MEDIUM
    analyze_now: bool = True


class CompetitorUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=12)
    notes: str | None = Field(default=None, max_length=5000)
    importance: Importance | None = None
    status: CompetitorStatus | None = None
    monitoring_enabled: bool | None = None
    monitoring_interval_hours: int | None = Field(default=None, ge=6, le=720)


class CompetitorResponse(APIModel):
    id: uuid.UUID
    name: str
    website_url: str
    domain: str
    favicon_url: str | None
    category: str | None
    tags: list[str]
    notes: str | None
    status: CompetitorStatus
    importance: Importance
    monitoring_enabled: bool
    monitoring_interval_hours: int
    next_monitor_at: datetime | None
    last_analyzed_at: datetime | None
    latest_overall_score: float | None
    created_at: datetime
    updated_at: datetime


class CompetitorPageResponse(APIModel):
    id: uuid.UUID
    url: str
    page_type: PageType
    title: str | None
    last_status_code: int | None
    last_fetched_at: datetime | None


# ---------------------------------------------------------------------- analysis


class EvidenceResponse(APIModel):
    quote: str | None = None
    source_url: str | None = None


class InsightResponse(APIModel):
    title: str
    detail: str
    evidence: EvidenceResponse | None = None


class RecommendationResponse(APIModel):
    title: str
    rationale: str
    priority: Literal["low", "medium", "high"]
    effort: Literal["low", "medium", "high"]


class AnalysisResponse(APIModel):
    id: uuid.UUID
    competitor_id: uuid.UUID
    created_at: datetime
    # Provenance of the analysis itself. The UI shows a banner when is_mock is true.
    provider: str
    model: str
    is_mock: bool
    depth: AnalysisDepth
    summary: str | None
    positioning: str | None
    target_audience: list[str]
    value_propositions: list[str]
    strengths: list[dict[str, Any]]
    weaknesses: list[dict[str, Any]]
    marketing_channels: list[str]
    recommendations: list[dict[str, Any]]
    key_features: list[str]
    confidence: float | None
    data_completeness: float | None
    pages_analyzed: int
    # Corrections the pipeline applied, e.g. a price discarded for not appearing on a page.
    data_notes: list[str]
    # Non-empty when a crawled page contained instruction-like text aimed at the model.
    injection_flags: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int


class ProductResponse(APIModel):
    id: uuid.UUID
    name: str
    description: str | None
    category: str | None
    features: list[str]
    source_url: str | None
    source: DataSource
    is_current: bool
    first_seen_at: datetime
    last_seen_at: datetime


class PricingPlanResponse(APIModel):
    id: uuid.UUID
    name: str
    amount: Decimal | None
    currency: str | None
    billing_period: BillingPeriod
    is_custom_pricing: bool
    is_free: bool
    features: list[str]
    highlights: str | None
    source_url: str | None
    source: DataSource
    is_current: bool
    first_seen_at: datetime
    last_seen_at: datetime


class ScoreDimensionResponse(APIModel):
    score: float | None
    weight: float
    rationale: str
    inputs: dict[str, Any]


class ScoreResponse(APIModel):
    id: uuid.UUID
    competitor_id: uuid.UUID
    overall: float | None
    dimensions: dict[str, ScoreDimensionResponse]
    threat_level: str
    confidence: float | None
    data_completeness: float | None
    methodology_version: str
    created_at: datetime


class CompetitorDetailResponse(APIModel):
    competitor: CompetitorResponse
    analysis: AnalysisResponse | None
    score: ScoreResponse | None
    products: list[ProductResponse]
    pricing: list[PricingPlanResponse]
    pages: list[CompetitorPageResponse]
    running_job: JobResponse | None = None


# -------------------------------------------------------------------- jobs


class AnalyzeRequest(APIModel):
    depth: AnalysisDepth = AnalysisDepth.STANDARD


class JobResponse(APIModel):
    id: uuid.UUID
    competitor_id: uuid.UUID | None
    job_type: JobType
    status: JobStatus
    depth: AnalysisDepth
    progress: int
    stage: str | None
    attempts: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------------- changes


class ChangeResponse(APIModel):
    id: uuid.UUID
    competitor_id: uuid.UUID
    change_type: ChangeType
    severity: Severity
    title: str
    description: str | None
    entity_key: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    magnitude: float | None
    source_url: str | None
    detected_at: datetime
    acknowledged_at: datetime | None


# -------------------------------------------------------------------- alerts


class AlertRuleCreate(APIModel):
    name: str = Field(min_length=1, max_length=120)
    competitor_id: uuid.UUID | None = None
    change_types: list[ChangeType] = Field(default_factory=list)
    min_severity: Severity = Severity.MEDIUM
    channels: list[NotificationChannel] = Field(
        default_factory=lambda: [NotificationChannel.IN_APP]
    )
    webhook_url: str | None = Field(default=None, max_length=2048)


class AlertRuleUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    change_types: list[ChangeType] | None = None
    min_severity: Severity | None = None
    channels: list[NotificationChannel] | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)


class AlertRuleResponse(APIModel):
    id: uuid.UUID
    name: str
    competitor_id: uuid.UUID | None
    is_active: bool
    change_types: list[str]
    min_severity: Severity
    channels: list[str]
    webhook_url: str | None
    created_at: datetime


class NotificationResponse(APIModel):
    id: uuid.UUID
    change_id: uuid.UUID | None
    channel: NotificationChannel
    status: str
    title: str
    body: str | None
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class MarkReadRequest(APIModel):
    notification_ids: list[uuid.UUID] | None = None


# ---------------------------------------------------------------- comparison


class ComparisonCreate(APIModel):
    competitor_ids: list[uuid.UUID] = Field(min_length=2, max_length=6)
    name: str | None = Field(default=None, max_length=160)
    with_insights: bool = True

    @field_validator("competitor_ids")
    @classmethod
    def _unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Each competitor can only appear once in a comparison.")
        return value


class ComparisonResponse(APIModel):
    id: uuid.UUID
    name: str | None
    competitor_ids: list[uuid.UUID]
    # Deterministic, computed from stored scores.
    matrix: dict[str, Any]
    # AI narrative. Null when no provider was available or the call failed.
    insights: dict[str, Any] | None
    provider: str | None
    is_mock: bool
    created_at: datetime


# ------------------------------------------------------------------- reports


class ReportCreate(APIModel):
    report_type: ReportType
    competitor_ids: list[uuid.UUID] = Field(default_factory=list, max_length=6)
    period_days: int = Field(default=30, ge=1, le=365)
    title: str | None = Field(default=None, max_length=200)


class ReportResponse(APIModel):
    id: uuid.UUID
    report_type: ReportType
    status: JobStatus
    title: str
    params: dict[str, Any]
    content: dict[str, Any] | None
    period_start: datetime | None
    period_end: datetime | None
    generated_at: datetime | None
    created_at: datetime


# -------------------------------------------------------------------- search


class SearchHitResponse(APIModel):
    competitor_id: uuid.UUID
    competitor_name: str
    content: str
    source_url: str | None
    similarity: float


class SearchResponse(APIModel):
    query: str
    hits: list[SearchHitResponse]
    provider: str
    # True when the offline hashing embedder produced the index: matches are lexical,
    # not semantic. The UI must say so rather than implying understanding.
    is_lexical: bool


# ----------------------------------------------------------------- dashboard


class DashboardResponse(APIModel):
    competitors_tracked: int
    competitors_analyzed: int
    competitors_archived: int
    average_score: float | None
    threat_distribution: dict[str, int]
    overall_threat_level: str
    changes_last_7_days: int
    high_severity_changes: int
    running_jobs: int
    failed_jobs: int
    uses_mock_ai: bool
    recent_changes: list[dict[str, Any]]
    recent_analyses: list[dict[str, Any]]
    landscape: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]


CompetitorDetailResponse.model_rebuild()

__all__ = [name for name in dir() if name.endswith(("Request", "Response", "Create", "Update"))]
