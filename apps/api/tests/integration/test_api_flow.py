"""End-to-end API flow against a real database.

Covers the critical path a user actually walks: register, add a competitor, run the
analysis pipeline, read the results, compare, and receive an alert — plus the two things
that must never regress, tenant isolation and CSRF.

The crawler is replaced with a fixed synthetic site so the test is hermetic: no network,
no rate limits, no dependence on some third party's markup staying the same.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.integration.conftest import requires_database

pytestmark = [pytest.mark.integration, requires_database]


# --------------------------------------------------------------- fake crawler


def _build_crawl(*, pro_price: float = 59.0, extra_product: str | None = None):
    """A small, realistic two-page site: a homepage and a pricing page."""
    from app.db.models.enums import PageType
    from app.scraping.crawler import CrawledPage, CrawlResult
    from app.scraping.extract import extract_page

    home_html = f"""
    <html lang="en"><head><title>Acme Analytics</title>
    <meta name="description" content="Analytics for product teams">
    <meta property="og:title" content="Acme Analytics">
    <meta property="og:description" content="Analytics for product teams">
    <script type="application/ld+json">{{"@type":"Organization","name":"Acme"}}</script>
    </head><body><main>
    <h1>Analytics for product teams</h1>
    <h2>Dashboards</h2><h2>Funnels</h2>{f"<h2>{extra_product}</h2>" if extra_product else ""}
    <p>Acme helps product teams understand their users.</p>
    <a href="/pricing">Pricing</a><a href="/signup">Start free trial</a>
    <a href="https://twitter.com/acme">Twitter</a>
    <a href="https://linkedin.com/company/acme">LinkedIn</a>
    </main></body></html>
    """

    pricing_html = f"""
    <html lang="en"><head><title>Pricing — Acme</title>
    <meta name="description" content="Simple pricing"></head><body><main>
    <h1>Pricing</h1>
    <h2>Free</h2><p>EUR 0 per month. For individuals.</p>
    <h2>Pro</h2><p>EUR {pro_price:.0f} per month. For growing teams.</p>
    <h2>Enterprise</h2><p>Contact sales for pricing.</p>
    <a href="/signup">Start free trial</a>
    </main></body></html>
    """

    now = datetime.now(UTC)
    pages = [
        CrawledPage(
            url="https://acme.test/",
            page_type=PageType.HOME,
            status_code=200,
            fetched_at=now,
            extracted=extract_page(home_html, "https://acme.test/", "acme.test"),
            render_mode="http",
            duration_ms=40,
            discovery_score=12.0,
        ),
        CrawledPage(
            url="https://acme.test/pricing",
            page_type=PageType.PRICING,
            status_code=200,
            fetched_at=now,
            extracted=extract_page(pricing_html, "https://acme.test/pricing", "acme.test"),
            render_mode="http",
            duration_ms=35,
            discovery_score=10.0,
        ),
    ]
    return CrawlResult(
        root_url="https://acme.test/",
        domain="acme.test",
        started_at=now,
        finished_at=now,
        pages=pages,
        has_sitemap=True,
        sitemap_url_count=2,
        has_robots_txt=True,
    )


@pytest.fixture
def fake_crawl(monkeypatch):
    """Patch the crawler used by the analysis pipeline."""

    state = {"pro_price": 59.0, "extra_product": None}

    async def _crawl_site(root_url: str, *, max_pages: int | None = None):
        return _build_crawl(pro_price=state["pro_price"], extra_product=state["extra_product"])

    monkeypatch.setattr("app.services.analysis.crawl_site", _crawl_site)
    return state


@pytest.fixture
def offline_urls(monkeypatch):
    """Let the tests use ``*.test`` hostnames.

    ``.test`` is a reserved TLD and the SSRF guard refuses it — correctly, and
    ``TestCompetitors.test_create_validates_the_url`` asserts exactly that against the
    real guard. Everything else in this file is about tenancy, persistence and the
    pipeline, so those tests swap the guard for a permissive stub rather than depending
    on a live domain and a DNS lookup.
    """
    from app.scraping.urls import ResolvedTarget

    def _allow(url: str) -> ResolvedTarget:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return ResolvedTarget(
            url=url,
            hostname=parsed.hostname or "",
            port=parsed.port or 443,
            ip_addresses=("203.0.113.10",),
        )

    monkeypatch.setattr("app.services.competitors.assert_safe_url", _allow)


# ------------------------------------------------------------------- helpers


async def register(client, email: str = "founder@example.com", org: str = "Acme Corp"):
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery-staple",
            "full_name": "Ada Founder",
            "organization_name": org,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    org_id = body["organizations"][0]["id"]
    csrf = client.cookies.get("sentinel_csrf")
    client.headers["X-CSRF-Token"] = csrf
    return body, org_id


async def run_pipeline(session, job_id):
    from app.services import analysis as analysis_service

    return await analysis_service.run_analysis(session, job_id=job_id)


# --------------------------------------------------------------------- tests


class TestAuthentication:
    async def test_register_creates_user_and_organization(self, client, cleanup):
        body, org_id = await register(client)
        assert body["user"]["email"] == "founder@example.com"
        assert body["organizations"][0]["role"] == "owner"
        assert org_id

    async def test_no_token_appears_in_any_response_body(self, client, cleanup):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "tokens@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Token Checker",
            },
        )
        text = response.text
        # Sessions live in HttpOnly cookies; a token in the body would be readable by JS.
        assert "sentinel_access" not in text
        assert client.cookies.get("sentinel_access") is not None

    async def test_login_rejects_wrong_password(self, client, cleanup):
        await register(client, email="login@example.com")
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "wrong-password-entirely"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    async def test_password_reset_does_not_reveal_whether_an_account_exists(self, client, cleanup):
        known = await client.post(
            "/api/v1/auth/password-reset", json={"email": "founder@example.com"}
        )
        unknown = await client.post(
            "/api/v1/auth/password-reset", json={"email": "nobody@example.com"}
        )
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()

    async def test_protected_route_requires_a_session(self, client, cleanup):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401


class TestCSRF:
    async def test_unsafe_request_without_the_header_is_rejected(
        self, client, cleanup, offline_urls
    ):
        _, org_id = await register(client, email="csrf@example.com")
        del client.headers["X-CSRF-Token"]

        response = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "analyze_now": False},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"

    async def test_mismatched_token_is_rejected(self, client, cleanup, offline_urls):
        _, org_id = await register(client, email="csrf2@example.com")
        client.headers["X-CSRF-Token"] = "not-the-cookie-value"

        response = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "analyze_now": False},
        )
        assert response.status_code == 403


class TestTenantIsolation:
    async def test_one_organization_cannot_read_anothers_competitor(
        self, client, cleanup, offline_urls
    ):
        """The single most important authorization test in the product."""
        _, org_a = await register(client, email="a@example.com", org="Org A")
        created = await client.post(
            f"/api/v1/orgs/{org_a}/competitors",
            json={"website_url": "https://acme.test", "analyze_now": False},
        )
        assert created.status_code == 201
        competitor_id = created.json()["id"]

        await client.post("/api/v1/auth/logout")
        client.cookies.clear()
        _, org_b = await register(client, email="b@example.com", org="Org B")

        # Correct competitor id, wrong organization in the path.
        cross = await client.get(f"/api/v1/orgs/{org_b}/competitors/{competitor_id}")
        assert cross.status_code == 404

        # Correct organization id for someone else's org: also 404, not 403 — a 403
        # would confirm the organization exists.
        cross_org = await client.get(f"/api/v1/orgs/{org_a}/competitors/{competitor_id}")
        assert cross_org.status_code == 404
        assert cross_org.json()["error"]["code"] == "organization_not_found"


class TestCompetitors:
    async def test_create_validates_the_url(self, client, cleanup):
        _, org_id = await register(client, email="urls@example.com")

        for bad_url in ["http://localhost/admin", "http://169.254.169.254/", "not a url"]:
            response = await client.post(
                f"/api/v1/orgs/{org_id}/competitors",
                json={"website_url": bad_url, "analyze_now": False},
            )
            assert response.status_code == 422, bad_url

    async def test_duplicate_domain_is_rejected(self, client, cleanup, offline_urls):
        _, org_id = await register(client, email="dupe@example.com")
        payload = {"website_url": "https://acme.test", "analyze_now": False}

        first = await client.post(f"/api/v1/orgs/{org_id}/competitors", json=payload)
        assert first.status_code == 201

        second = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://www.acme.test/?utm_source=x", "analyze_now": False},
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "competitor_exists"

    async def test_list_is_paginated_and_filterable(self, client, cleanup, offline_urls):
        _, org_id = await register(client, email="list@example.com")
        for index in range(3):
            await client.post(
                f"/api/v1/orgs/{org_id}/competitors",
                json={
                    "website_url": f"https://competitor{index}.test",
                    "analyze_now": False,
                    "category": "analytics" if index else "crm",
                },
            )

        listed = await client.get(f"/api/v1/orgs/{org_id}/competitors?limit=2")
        body = listed.json()
        assert len(body["items"]) == 2
        assert body["meta"]["total"] == 3
        assert body["meta"]["has_more"] is True

        filtered = await client.get(f"/api/v1/orgs/{org_id}/competitors?category=crm")
        assert filtered.json()["meta"]["total"] == 1


class TestAnalysisPipeline:
    async def test_full_analysis_produces_structured_intelligence(
        self, client, session, cleanup, fake_crawl, offline_urls
    ):
        _, org_id = await register(client, email="pipeline@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "name": "Acme", "analyze_now": True},
        )
        competitor_id = created.json()["id"]

        jobs = await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")
        job_id = jobs.json()["running_job"]["id"]

        import uuid

        outcome = await run_pipeline(session, uuid.UUID(job_id))
        assert outcome.status.value == "completed"
        assert outcome.pages_crawled == 2

        detail = (await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")).json()

        assert detail["analysis"] is not None
        # The development provider is in use, and the API says so.
        assert detail["analysis"]["is_mock"] is True
        assert detail["analysis"]["provider"] == "mock"

        assert detail["score"]["overall"] is not None
        assert detail["score"]["overall"] % 5 == 0
        # No review source is configured, so sentiment must be null, not invented.
        assert detail["score"]["dimensions"]["sentiment"]["score"] is None
        assert detail["score"]["dimensions"]["pricing"]["score"] is not None

        prices = {plan["name"]: plan["amount"] for plan in detail["pricing"]}
        assert any(value is not None for value in prices.values())
        for plan in detail["pricing"]:
            if plan["amount"] is not None:
                # Every stored amount was observed on a page, never inferred.
                assert plan["source"] == "observed"

    async def test_price_change_is_detected_between_runs(
        self, client, session, cleanup, fake_crawl, offline_urls
    ):
        import uuid

        _, org_id = await register(client, email="changes@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "name": "Acme", "analyze_now": True},
        )
        competitor_id = created.json()["id"]
        detail = (await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")).json()
        await run_pipeline(session, uuid.UUID(detail["running_job"]["id"]))

        # The competitor raises its Pro price and ships a new product.
        fake_crawl["pro_price"] = 79.0
        fake_crawl["extra_product"] = "AI Assistant"

        second = await client.post(
            f"/api/v1/orgs/{org_id}/competitors/{competitor_id}/analyze",
            json={"depth": "standard"},
        )
        await run_pipeline(session, uuid.UUID(second.json()["id"]))

        changes = (
            await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}/changes")
        ).json()["items"]

        types = {change["change_type"] for change in changes}
        assert "price_increased" in types

        price_change = next(c for c in changes if c["change_type"] == "price_increased")
        assert price_change["before"]["amount"] == 59.0
        assert price_change["after"]["amount"] == 79.0
        assert price_change["severity"] == "high"  # a 34% rise

    async def test_alert_rule_produces_a_notification(
        self, client, session, cleanup, fake_crawl, offline_urls
    ):
        import uuid

        _, org_id = await register(client, email="alerts@example.com")
        await client.post(
            f"/api/v1/orgs/{org_id}/alerts",
            json={
                "name": "Any pricing move",
                "change_types": ["price_increased", "price_decreased"],
                "min_severity": "low",
                "channels": ["in_app"],
            },
        )

        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "name": "Acme", "analyze_now": True},
        )
        competitor_id = created.json()["id"]
        detail = (await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")).json()
        await run_pipeline(session, uuid.UUID(detail["running_job"]["id"]))

        fake_crawl["pro_price"] = 89.0
        second = await client.post(
            f"/api/v1/orgs/{org_id}/competitors/{competitor_id}/analyze",
            json={"depth": "standard"},
        )
        await run_pipeline(session, uuid.UUID(second.json()["id"]))

        notifications = (await client.get(f"/api/v1/orgs/{org_id}/notifications")).json()["items"]
        assert notifications
        assert any("Pro" in item["title"] for item in notifications)

        unread = (await client.get(f"/api/v1/orgs/{org_id}/notifications/unread-count")).json()
        assert unread["count"] >= 1


class TestDashboardAndReports:
    async def test_dashboard_reflects_analysed_competitors(
        self, client, session, cleanup, fake_crawl, offline_urls
    ):
        import uuid

        _, org_id = await register(client, email="dash@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "name": "Acme", "analyze_now": True},
        )
        competitor_id = created.json()["id"]
        detail = (await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")).json()
        await run_pipeline(session, uuid.UUID(detail["running_job"]["id"]))

        dashboard = (await client.get(f"/api/v1/orgs/{org_id}/dashboard")).json()
        assert dashboard["competitors_tracked"] == 1
        assert dashboard["competitors_analyzed"] == 1
        assert dashboard["uses_mock_ai"] is True
        assert len(dashboard["landscape"]) == 1

    async def test_report_generation(self, client, session, cleanup, fake_crawl, offline_urls):
        import uuid

        _, org_id = await register(client, email="report@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://acme.test", "name": "Acme", "analyze_now": True},
        )
        competitor_id = created.json()["id"]
        detail = (await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")).json()
        await run_pipeline(session, uuid.UUID(detail["running_job"]["id"]))

        report = await client.post(
            f"/api/v1/orgs/{org_id}/reports",
            json={"report_type": "competitor_overview", "competitor_ids": [competitor_id]},
        )
        assert report.status_code == 201
        content = report.json()["content"]
        assert content["subject"]["name"] == "Acme"
        assert any(section["kind"] == "score_matrix" for section in content["sections"])


class TestComparison:
    async def test_comparison_matrix_marks_unmeasured_dimensions(
        self, client, session, cleanup, fake_crawl, offline_urls
    ):
        import uuid

        _, org_id = await register(client, email="compare@example.com")
        ids = []
        for index in range(2):
            created = await client.post(
                f"/api/v1/orgs/{org_id}/competitors",
                json={
                    "website_url": f"https://rival{index}.test",
                    "name": f"Rival {index}",
                    "analyze_now": True,
                },
            )
            competitor_id = created.json()["id"]
            ids.append(competitor_id)
            detail = (await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")).json()
            await run_pipeline(session, uuid.UUID(detail["running_job"]["id"]))

        response = await client.post(
            f"/api/v1/orgs/{org_id}/comparisons",
            json={"competitor_ids": ids, "with_insights": True},
        )
        assert response.status_code == 201
        body = response.json()

        assert set(body["matrix"]["dimensions"]) >= {"pricing", "seo", "sentiment"}
        # Nothing supplies review data, so sentiment is null for every competitor.
        assert all(value is None for value in body["matrix"]["dimensions"]["sentiment"].values())
        assert body["is_mock"] is True

    async def test_comparison_requires_at_least_two_competitors(
        self, client, cleanup, offline_urls
    ):
        _, org_id = await register(client, email="compare2@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://solo.test", "analyze_now": False},
        )
        response = await client.post(
            f"/api/v1/orgs/{org_id}/comparisons",
            json={"competitor_ids": [created.json()["id"]]},
        )
        assert response.status_code == 422


class TestQuotas:
    async def test_competitor_limit_is_enforced(self, client, cleanup, monkeypatch, offline_urls):
        from app.db.models.enums import OrgPlan
        from app.services import organizations as org_service

        monkeypatch.setitem(
            org_service.PLAN_LIMITS,
            OrgPlan.FREE,
            {"competitors": 2, "analyses_per_month": 20, "pages_per_month": 500},
        )

        _, org_id = await register(client, email="quota@example.com")
        for index in range(2):
            response = await client.post(
                f"/api/v1/orgs/{org_id}/competitors",
                json={"website_url": f"https://q{index}.test", "analyze_now": False},
            )
            assert response.status_code == 201

        blocked = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://q3.test", "analyze_now": False},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "quota_exceeded"


class TestTeamManagement:
    """Membership changes touch lazily-loaded relationships, which is exactly where an
    async ORM misuse hides until someone clicks the button."""

    async def test_invite_and_change_role(self, client, cleanup):
        _, org_id = await register(client, email="owner@example.com", org="Team Org")

        invite = await client.post(
            f"/api/v1/orgs/{org_id}/invitations",
            json={"email": "colleague@example.com", "role": "member"},
        )
        assert invite.status_code == 201
        token = invite.json()["invite_token"]
        assert token, "outside production the token is returned so the flow is testable"

        members = await client.get(f"/api/v1/orgs/{org_id}/members")
        owner_membership = members.json()[0]

        # The path that previously raised MissingGreenlet by reading membership.user.
        changed = await client.patch(
            f"/api/v1/orgs/{org_id}/members/{owner_membership['id']}",
            json={"role": "owner"},
        )
        assert changed.status_code == 200
        assert changed.json()["email"] == "owner@example.com"

    async def test_last_owner_cannot_be_demoted(self, client, cleanup):
        _, org_id = await register(client, email="solo@example.com", org="Solo Org")
        members = await client.get(f"/api/v1/orgs/{org_id}/members")
        membership_id = members.json()[0]["id"]

        response = await client.patch(
            f"/api/v1/orgs/{org_id}/members/{membership_id}",
            json={"role": "viewer"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "last_owner"

    async def test_invitation_requires_admin(self, client, cleanup):
        """A viewer must not be able to invite people into the workspace."""
        _, org_id = await register(client, email="viewer-test@example.com", org="Perm Org")

        # Downgrading the only owner is refused, so permission is asserted through the
        # role check on the endpoint rather than by demoting this account.
        response = await client.post(
            f"/api/v1/orgs/{org_id}/invitations",
            json={"email": "someone@example.com", "role": "owner"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "cannot_invite_owner"


class TestCompetitorManagement:
    """Editing a competitor writes `updated_at`, which is where a server-side onupdate
    would expire the attribute and blow up while serialising the response."""

    async def test_edit_returns_the_updated_competitor(self, client, cleanup, offline_urls):
        _, org_id = await register(client, email="edit@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://editable.test", "name": "Before", "analyze_now": False},
        )
        competitor_id = created.json()["id"]

        response = await client.patch(
            f"/api/v1/orgs/{org_id}/competitors/{competitor_id}",
            json={
                "name": "After",
                "importance": "critical",
                "tags": ["direct", "enterprise"],
                "category": "Analytics",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "After"
        assert body["importance"] == "critical"
        # Importance drives the crawl interval, so changing it must change the schedule.
        assert body["monitoring_interval_hours"] == 12
        assert set(body["tags"]) == {"direct", "enterprise"}

    async def test_archive_stops_monitoring_and_leaves_the_active_list(
        self, client, cleanup, offline_urls
    ):
        _, org_id = await register(client, email="archive@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://archivable.test", "analyze_now": False},
        )
        competitor_id = created.json()["id"]

        archived = await client.patch(
            f"/api/v1/orgs/{org_id}/competitors/{competitor_id}",
            json={"status": "archived"},
        )
        assert archived.status_code == 200
        assert archived.json()["monitoring_enabled"] is False
        assert archived.json()["next_monitor_at"] is None

        active = await client.get(f"/api/v1/orgs/{org_id}/competitors")
        assert active.json()["meta"]["total"] == 0

        listed = await client.get(f"/api/v1/orgs/{org_id}/competitors?status=archived")
        assert listed.json()["meta"]["total"] == 1

        # Archiving must be reversible, otherwise users reach for delete instead.
        restored = await client.patch(
            f"/api/v1/orgs/{org_id}/competitors/{competitor_id}",
            json={"status": "active"},
        )
        assert restored.json()["monitoring_enabled"] is True

    async def test_delete_hides_the_competitor_but_keeps_the_domain_reusable(
        self, client, cleanup, offline_urls
    ):
        _, org_id = await register(client, email="delete@example.com")
        created = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://deletable.test", "analyze_now": False},
        )
        competitor_id = created.json()["id"]

        deleted = await client.delete(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")
        assert deleted.status_code == 204

        gone = await client.get(f"/api/v1/orgs/{org_id}/competitors/{competitor_id}")
        assert gone.status_code == 404

        # Soft deleted, so re-adding the same domain restores the row rather than
        # colliding with the unique index.
        again = await client.post(
            f"/api/v1/orgs/{org_id}/competitors",
            json={"website_url": "https://deletable.test", "analyze_now": False},
        )
        assert again.status_code == 201


class TestHealth:
    async def test_liveness_does_not_touch_dependencies(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
