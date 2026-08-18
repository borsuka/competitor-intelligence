"""ORM models.

Importing this package registers every table on :class:`app.db.base.Base.metadata`,
which is what Alembic autogenerate reads.  Import order matters only in that all modules
must be imported before ``metadata`` is inspected.
"""

from app.db.base import Base
from app.db.models.analysis import (
    Analysis,
    AnalysisJob,
    Embedding,
    PricingPlan,
    Product,
    Score,
)
from app.db.models.competitor import (
    Competitor,
    CompetitorPage,
    PageSnapshot,
    SeoSnapshot,
)
from app.db.models.identity import (
    AuditLog,
    Invitation,
    Membership,
    Organization,
    RefreshToken,
    UsageCounter,
    User,
    VerificationToken,
)
from app.db.models.monitoring import (
    AlertRule,
    Change,
    Comparison,
    Notification,
    Report,
)

__all__ = [
    "Base",
    # identity
    "User",
    "RefreshToken",
    "VerificationToken",
    "Organization",
    "Membership",
    "Invitation",
    "UsageCounter",
    "AuditLog",
    # competitor
    "Competitor",
    "CompetitorPage",
    "PageSnapshot",
    "SeoSnapshot",
    # analysis
    "AnalysisJob",
    "Analysis",
    "Product",
    "PricingPlan",
    "Score",
    "Embedding",
    # monitoring
    "Change",
    "AlertRule",
    "Notification",
    "Comparison",
    "Report",
]
