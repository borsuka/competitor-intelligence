"""Development seed data.

Creates a demo account with three competitors, analyses, scores and a change history, so
the UI can be developed without crawling anyone.

Everything it writes is **clearly labelled as demo data**: competitor names are prefixed
with ``[DEMO]``, the domains are under ``example.com`` (reserved by RFC 2606 and owned by
IANA, so they can never collide with a real company), and every analysis is marked
``is_mock``. Nothing here can be mistaken for real competitive intelligence.

Refuses to run against a production environment.

    python scripts/seed.py
    python scripts/seed.py --reset      # delete existing demo data first
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.core.tenancy import Role
from app.db.base import utcnow
from app.db.models.analysis import Analysis, PricingPlan, Product, Score
from app.db.models.competitor import Competitor, CompetitorPage, PageSnapshot
from app.db.models.enums import (
    AnalysisDepth,
    BillingPeriod,
    ChangeType,
    CompetitorStatus,
    DataSource,
    Importance,
    NotificationChannel,
    PageType,
    Severity,
)
from app.db.models.identity import Membership, Organization, User
from app.db.models.monitoring import AlertRule, Change
from app.db.session import dispose_engine, session_scope
from app.services.scoring import ScoringInput, compute_score

log = get_logger(__name__)

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-password-for-local-use"  # noqa: S105 - local seed only
DEMO_PREFIX = "[DEMO]"

# example.com is reserved by RFC 2606; these can never be a real company's domain.
COMPETITORS = [
    {
        "name": f"{DEMO_PREFIX} Northwind Analytics",
        "domain": "northwind.example.com",
        "category": "Analytics",
        "importance": Importance.CRITICAL,
        "summary": (
            "Demo data. Northwind Analytics positions itself as a privacy-first product "
            "analytics tool for mid-market SaaS teams, leading with ease of setup and "
            "EU data residency."
        ),
        "products": [
            (
                "Product Analytics",
                "Event tracking and funnels.",
                ["Funnels", "Retention", "Cohorts"],
            ),
            ("Session Replay", "Watch real user sessions.", ["Privacy masking", "Console logs"]),
        ],
        "plans": [
            ("Free", Decimal("0"), True, False),
            ("Growth", Decimal("89"), False, False),
            ("Scale", Decimal("249"), False, False),
            ("Enterprise", None, False, True),
        ],
        "features": [
            "Funnels",
            "Retention",
            "Cohorts",
            "Session replay",
            "EU hosting",
            "SSO",
            "API",
        ],
    },
    {
        "name": f"{DEMO_PREFIX} Harbour Metrics",
        "domain": "harbour.example.com",
        "category": "Analytics",
        "importance": Importance.HIGH,
        "summary": (
            "Demo data. Harbour Metrics targets enterprise data teams with a warehouse-native "
            "model. Pricing is not published; the site routes everything through sales."
        ),
        "products": [
            ("Warehouse Analytics", "Runs on your own warehouse.", ["dbt models", "SQL access"]),
        ],
        "plans": [("Enterprise", None, False, True)],
        "features": ["Warehouse-native", "SQL access", "dbt integration", "SOC 2"],
    },
    {
        "name": f"{DEMO_PREFIX} Pico Insights",
        "domain": "pico.example.com",
        "category": "Analytics",
        "importance": Importance.MEDIUM,
        "summary": (
            "Demo data. Pico Insights is a low-cost, single-founder analytics tool aimed at "
            "indie developers, with a one-time licence rather than a subscription."
        ),
        "products": [("Pico", "Lightweight web analytics.", ["Script under 2kb"])],
        "plans": [("Lifetime", Decimal("49"), False, False)],
        "features": ["Tiny script", "No cookies", "One-time payment"],
    },
]


async def seed(reset: bool) -> None:
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("Refusing to seed a production database.")

    async with session_scope() as session:
        if reset:
            await _reset(session)

        existing = await session.execute(select(User).where(User.email == DEMO_EMAIL))
        user = existing.scalar_one_or_none()

        if user is None:
            user = User(
                email=DEMO_EMAIL,
                hashed_password=hash_password(DEMO_PASSWORD),
                full_name="Demo User",
                email_verified_at=utcnow(),
            )
            session.add(user)
            await session.flush()

            organization = Organization(
                name="Demo Workspace",
                slug=f"demo-{uuid.uuid4().hex[:6]}",
                own_company_name="Our Company",
                own_company_url="https://ourcompany.example.com",
                own_company_description=(
                    "Demo data. We sell a mid-market analytics product to product teams, "
                    "positioned on speed of setup."
                ),
            )
            session.add(organization)
            await session.flush()
            session.add(
                Membership(organization_id=organization.id, user_id=user.id, role=Role.OWNER)
            )
            await session.flush()
        else:
            membership = await session.execute(
                select(Membership).where(Membership.user_id == user.id)
            )
            organization = await session.get(
                Organization, membership.scalars().first().organization_id
            )

        # Deterministic, so re-seeding produces the same demo state.
        rng = random.Random(20260101)

        for spec in COMPETITORS:
            await _seed_competitor(session, organization, user, spec, rng)

        session.add(
            AlertRule(
                organization_id=organization.id,
                created_by_user_id=user.id,
                name="Any pricing change",
                change_types=[ChangeType.PRICE_INCREASED.value, ChangeType.PRICE_DECREASED.value],
                min_severity=Severity.LOW,
                channels=[NotificationChannel.IN_APP.value],
            )
        )

        await session.commit()

    print("\nSeeded demo data.")
    print(f"  Sign in with: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print("  All competitors are prefixed [DEMO] and use RFC 2606 reserved domains.")
    print("  Every analysis is flagged is_mock and shows a banner in the UI.\n")


async def _seed_competitor(session, organization, user, spec, rng) -> None:
    existing = await session.execute(
        select(Competitor).where(
            Competitor.organization_id == organization.id, Competitor.domain == spec["domain"]
        )
    )
    if existing.scalar_one_or_none() is not None:
        return

    now = utcnow()
    competitor = Competitor(
        organization_id=organization.id,
        created_by_user_id=user.id,
        name=spec["name"],
        website_url=f"https://{spec['domain']}",
        domain=spec["domain"],
        category=spec["category"],
        importance=spec["importance"],
        tags=["demo"],
        status=CompetitorStatus.ACTIVE,
        notes="Seeded demo record. Not a real company.",
        last_analyzed_at=now - timedelta(hours=2),
    )
    session.add(competitor)
    await session.flush()

    for page_type, path in [
        (PageType.HOME, "/"),
        (PageType.PRICING, "/pricing"),
        (PageType.FEATURES, "/features"),
        (PageType.ABOUT, "/about"),
    ]:
        page = CompetitorPage(
            organization_id=organization.id,
            competitor_id=competitor.id,
            url=f"https://{spec['domain']}{path}",
            url_hash=uuid.uuid4().hex + uuid.uuid4().hex[:0],
            page_type=page_type,
            title=f"{spec['name']} — {page_type.value}",
            discovery_score=10.0,
            last_status_code=200,
            last_fetched_at=now - timedelta(hours=2),
        )
        session.add(page)
        await session.flush()
        session.add(
            PageSnapshot(
                organization_id=organization.id,
                competitor_id=competitor.id,
                page_id=page.id,
                fetched_at=now - timedelta(hours=2),
                http_status=200,
                content_hash=uuid.uuid4().hex,
                text_hash=uuid.uuid4().hex,
                title=page.title,
                meta_description="Demo seed page.",
                headings={"h1": [spec["name"]]},
                text_content=f"Demo content for {spec['name']}.",
                word_count=rng.randint(300, 900),
            )
        )

    analysis = Analysis(
        organization_id=organization.id,
        competitor_id=competitor.id,
        provider="mock",
        model="development-heuristics-v1",
        is_mock=True,
        depth=AnalysisDepth.STANDARD,
        summary=spec["summary"],
        positioning=spec["summary"].split(". ", 1)[-1],
        target_audience=["Product teams", "Mid-market SaaS"],
        value_propositions=["Fast setup", "Privacy-first"],
        strengths=[
            {"title": "Clear pricing", "detail": "Demo data: plans are published on the site."}
        ],
        weaknesses=[
            {"title": "Thin documentation", "detail": "Demo data: few developer resources."}
        ],
        marketing_channels=["Blog", "X", "LinkedIn"],
        key_features=spec["features"],
        confidence=0.35,
        pages_analyzed=4,
        data_notes=[],
        injection_flags=[],
        tokens_in=0,
        tokens_out=0,
    )
    session.add(analysis)
    await session.flush()
    competitor.latest_analysis_id = analysis.id

    for name, description, features in spec["products"]:
        session.add(
            Product(
                organization_id=organization.id,
                competitor_id=competitor.id,
                analysis_id=analysis.id,
                name=name,
                normalized_name=name.lower(),
                description=description,
                features=features,
                source=DataSource.AI_INFERENCE,
                first_seen_at=now - timedelta(days=30),
                last_seen_at=now,
            )
        )

    for plan_name, amount, is_free, is_custom in spec["plans"]:
        session.add(
            PricingPlan(
                organization_id=organization.id,
                competitor_id=competitor.id,
                analysis_id=analysis.id,
                name=plan_name,
                normalized_name=plan_name.lower(),
                amount=amount,
                currency="EUR" if amount is not None else None,
                billing_period=BillingPeriod.MONTHLY if amount else BillingPeriod.UNKNOWN,
                is_free=is_free,
                is_custom_pricing=is_custom,
                features=spec["features"][:4],
                source=DataSource.OBSERVED if amount is not None else DataSource.AI_INFERENCE,
                first_seen_at=now - timedelta(days=30),
                last_seen_at=now,
            )
        )

    plans = spec["plans"]
    result = compute_score(
        ScoringInput(
            product_count=len(spec["products"]),
            products_with_description=len(spec["products"]),
            feature_count=len(spec["features"]),
            features_per_product=3.0,
            pricing_plan_count=len(plans),
            plans_with_public_amount=sum(1 for _, amount, _, _ in plans if amount is not None),
            has_free_tier=any(free for _, _, free, _ in plans),
            has_custom_pricing=any(custom for _, _, _, custom in plans),
            pricing_page_found=True,
            value_proposition_count=2,
            has_positioning_statement=True,
            target_audience_count=2,
            ai_confidence=0.35,
            pages_crawled=4,
            has_sitemap=True,
            has_robots_txt=True,
            has_blog=True,
            structured_data_type_count=2,
            avg_word_count=650,
            internal_link_count=40,
            marketing_channel_count=3,
            social_profile_count=3,
            case_study_pages=1,
            cta_count=3,
            open_graph_tag_count=5,
            has_favicon=True,
            external_link_count=15,
        )
    )
    score = Score(
        organization_id=organization.id,
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
    competitor.latest_overall_score = result.overall

    # A little change history, so the monitoring feed is not empty in development.
    if spec["domain"].startswith("northwind"):
        session.add(
            Change(
                created_at=now - timedelta(days=2),
                organization_id=organization.id,
                competitor_id=competitor.id,
                analysis_id=analysis.id,
                change_type=ChangeType.PRICE_INCREASED,
                severity=Severity.HIGH,
                title=f"{spec['name']} increased Growth from EUR 79 to EUR 89",
                description="Demo data: a seeded example of a detected price change.",
                entity_key="growth|monthly",
                before={"name": "Growth", "amount": 79.0, "currency": "EUR"},
                after={"name": "Growth", "amount": 89.0, "currency": "EUR"},
                magnitude=0.1266,
                source_url=f"https://{spec['domain']}/pricing",
                detected_at=now - timedelta(days=2),
            )
        )


async def _reset(session) -> None:
    """Remove seeded records only. Never touches data the seeder did not create."""
    result = await session.execute(select(User).where(User.email == DEMO_EMAIL))
    user = result.scalar_one_or_none()
    if user is None:
        return

    memberships = await session.execute(
        select(Membership.organization_id).where(Membership.user_id == user.id)
    )
    org_ids = list(memberships.scalars().all())
    for org_id in org_ids:
        await session.execute(delete(Organization).where(Organization.id == org_id))
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()
    print("Removed existing demo data.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed development data.")
    parser.add_argument("--reset", action="store_true", help="delete existing demo data first")
    args = parser.parse_args()

    configure_logging()

    async def run() -> None:
        # Dispose inside the same event loop that opened the connections: a second
        # asyncio.run() would try to close sockets bound to a loop that no longer exists.
        try:
            await seed(args.reset)
        finally:
            await dispose_engine()

    asyncio.run(run())


if __name__ == "__main__":
    main()
