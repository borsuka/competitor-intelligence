"""Enumerations shared by the ORM models and the API schemas.

Stored as ``VARCHAR`` with a ``CHECK`` constraint (``native_enum=False``) rather than as
PostgreSQL enum types.  Adding a value to a native enum requires ``ALTER TYPE`` and
cannot run inside a transaction on older servers; a check constraint is a plain,
reversible migration.
"""

from __future__ import annotations

import enum


class OrgPlan(str, enum.Enum):
    FREE = "free"
    STARTER = "starter"
    GROWTH = "growth"
    ENTERPRISE = "enterprise"


class CompetitorStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Importance(str, enum.Enum):
    """How much a competitor matters to the user — drives monitoring frequency."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PageType(str, enum.Enum):
    HOME = "home"
    PRICING = "pricing"
    PRODUCT = "product"
    FEATURES = "features"
    ABOUT = "about"
    BLOG = "blog"
    CASE_STUDY = "case_study"
    DOCS = "docs"
    CONTACT = "contact"
    CAREERS = "careers"
    LEGAL = "legal"
    OTHER = "other"


class JobType(str, enum.Enum):
    FULL_ANALYSIS = "full_analysis"
    REFRESH = "refresh"
    COMPARISON = "comparison"
    REPORT = "report"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


class AnalysisDepth(str, enum.Enum):
    """Cost/quality dial. Controls page budget and which model is used."""

    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class ScoreDimension(str, enum.Enum):
    PRODUCT = "product"
    PRICING = "pricing"
    FEATURES = "features"
    POSITIONING = "positioning"
    SEO = "seo"
    MARKETING = "marketing"
    SENTIMENT = "sentiment"
    BRAND = "brand"


class ChangeType(str, enum.Enum):
    PRICE_INCREASED = "price_increased"
    PRICE_DECREASED = "price_decreased"
    PLAN_ADDED = "plan_added"
    PLAN_REMOVED = "plan_removed"
    PRODUCT_ADDED = "product_added"
    PRODUCT_REMOVED = "product_removed"
    FEATURE_ADDED = "feature_added"
    FEATURE_REMOVED = "feature_removed"
    POSITIONING_CHANGED = "positioning_changed"
    CONTENT_CHANGED = "content_changed"
    PAGE_ADDED = "page_added"
    PAGE_REMOVED = "page_removed"
    SCORE_CHANGED = "score_changed"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]


class DataSource(str, enum.Enum):
    """Provenance. The UI renders these two differently and must never confuse them."""

    OBSERVED = "observed"
    AI_INFERENCE = "ai_inference"


class NotificationChannel(str, enum.Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"


class ReportType(str, enum.Enum):
    COMPETITOR_OVERVIEW = "competitor_overview"
    COMPARISON = "comparison"
    WEEKLY_INTELLIGENCE = "weekly_intelligence"
    MONTHLY_COMPETITIVE = "monthly_competitive"


class BillingPeriod(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"
    ONE_TIME = "one_time"
    USAGE_BASED = "usage_based"
    UNKNOWN = "unknown"


class VerificationPurpose(str, enum.Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


__all__ = [
    "OrgPlan",
    "CompetitorStatus",
    "Importance",
    "PageType",
    "JobType",
    "JobStatus",
    "AnalysisDepth",
    "ScoreDimension",
    "ChangeType",
    "Severity",
    "DataSource",
    "NotificationChannel",
    "NotificationStatus",
    "ReportType",
    "BillingPeriod",
    "VerificationPurpose",
]
