"""Crawl orchestration: homepage → discovery → prioritised fetch → observations.

The crawler returns plain data.  It never touches the database, which keeps it usable
from a test, a script or a worker without a session, and keeps persistence decisions in
the service layer where transactions belong.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.errors import FetchError, UnsafeURLError
from app.core.logging import get_logger
from app.db.models.enums import PageType
from app.scraping.discovery import DiscoveredPage, classify_url, parse_sitemap_urls, rank_pages
from app.scraping.extract import ExtractedPage, extract_page
from app.scraping.fetcher import PageFetcher, build_fetcher
from app.scraping.robots import RobotsCache
from app.scraping.urls import assert_safe_url, extract_domain, normalize_url

log = get_logger(__name__)


@dataclass(slots=True)
class CrawledPage:
    url: str
    page_type: PageType
    status_code: int
    fetched_at: datetime
    extracted: ExtractedPage
    render_mode: str
    duration_ms: int
    discovery_score: float = 0.0


@dataclass(slots=True)
class CrawlError:
    url: str
    code: str
    message: str


@dataclass(slots=True)
class CrawlResult:
    """Everything one crawl observed. Pure data — safe to serialise and to test."""

    root_url: str
    domain: str
    started_at: datetime
    finished_at: datetime
    pages: list[CrawledPage] = field(default_factory=list)
    errors: list[CrawlError] = field(default_factory=list)
    discovered_urls: list[str] = field(default_factory=list)
    has_sitemap: bool = False
    sitemap_url_count: int = 0
    has_robots_txt: bool = False
    blocked_by_robots: int = 0

    @property
    def succeeded(self) -> bool:
        return bool(self.pages)

    def page_of_type(self, page_type: PageType) -> CrawledPage | None:
        for page in self.pages:
            if page.page_type is page_type:
                return page
        return None

    @property
    def total_words(self) -> int:
        return sum(page.extracted.word_count for page in self.pages)


class Crawler:
    """Crawls one competitor site within a page budget."""

    def __init__(
        self,
        *,
        fetcher: PageFetcher | None = None,
        robots: RobotsCache | None = None,
        max_pages: int | None = None,
    ) -> None:
        self._settings = get_settings()
        self._fetcher = fetcher or build_fetcher()
        self._owns_fetcher = fetcher is None
        self._robots = robots or RobotsCache()
        self._owns_robots = robots is None
        self._max_pages = max_pages or self._settings.scraper_max_pages

    async def crawl(self, root_url: str) -> CrawlResult:
        started = datetime.now(UTC)
        root = normalize_url(root_url)
        domain = extract_domain(root)

        result = CrawlResult(
            root_url=root, domain=domain, started_at=started, finished_at=started
        )

        # 1. Homepage.  If it cannot be fetched there is nothing to analyse, so this is
        #    the one failure that aborts the crawl rather than being collected.
        home = await self._fetch_one(root, PageType.HOME, 12.0, result)
        if home is None:
            result.finished_at = datetime.now(UTC)
            return result
        result.pages.append(home)

        # 2. Discovery from robots/sitemap plus the homepage's own links.
        candidates = list(home.extracted.internal_links)
        sitemap_urls = await self._read_sitemaps(root, domain, result)
        for url in sitemap_urls:
            candidates.append({"url": url, "text": ""})

        budget = self._max_pages - 1
        ranked: list[DiscoveredPage] = rank_pages(
            candidates,
            base_domain=domain,
            limit=budget,
            exclude={home.url, home.extracted.url},
        )
        result.discovered_urls = [page.url for page in ranked]

        # 3. Fetch, highest value first, sequentially.  Sequential is deliberate: the
        #    throttle already serialises per domain, and concurrency here would only add
        #    complexity while making us a worse citizen.
        for candidate in ranked:
            if len(result.pages) >= self._max_pages:
                break
            page = await self._fetch_one(
                candidate.url, candidate.page_type, candidate.score, result
            )
            if page is not None:
                result.pages.append(page)

        result.finished_at = datetime.now(UTC)
        log.info(
            "crawler.finished",
            domain=domain,
            pages=len(result.pages),
            errors=len(result.errors),
            duration_ms=int((result.finished_at - started).total_seconds() * 1000),
        )
        return result

    async def _fetch_one(
        self,
        url: str,
        page_type: PageType,
        score: float,
        result: CrawlResult,
    ) -> CrawledPage | None:
        try:
            if not await self._robots.allows(url):
                result.blocked_by_robots += 1
                return None
        except Exception as exc:  # noqa: BLE001 - robots must never break a crawl
            log.debug("crawler.robots_check_failed", url=url, error=str(exc))

        try:
            fetched = await self._fetcher.fetch(url)
        except (FetchError, UnsafeURLError) as exc:
            result.errors.append(CrawlError(url=url, code=exc.code, message=exc.message))
            return None

        if fetched.status_code >= 400:
            result.errors.append(
                CrawlError(
                    url=url,
                    code="http_error",
                    message=f"The page returned HTTP {fetched.status_code}.",
                )
            )
            return None

        extracted = extract_page(fetched.html, fetched.final_url, result.domain)

        # A redirect can land somewhere with a different purpose than the link suggested.
        if page_type is not PageType.HOME and fetched.final_url != fetched.url:
            page_type, _ = classify_url(fetched.final_url)

        return CrawledPage(
            url=fetched.final_url,
            page_type=page_type,
            status_code=fetched.status_code,
            fetched_at=datetime.now(UTC),
            extracted=extracted,
            render_mode=fetched.render_mode,
            duration_ms=fetched.elapsed_ms,
            discovery_score=score,
        )

    async def _read_sitemaps(self, root: str, domain: str, result: CrawlResult) -> list[str]:
        """Best-effort sitemap read. A missing sitemap is a data point, not an error."""
        try:
            policy = await self._robots.get(root)
            result.has_robots_txt = policy.fetched
            sitemap_urls = await self._robots.sitemaps_for(root)
        except Exception as exc:  # noqa: BLE001
            log.debug("crawler.sitemap_lookup_failed", url=root, error=str(exc))
            return []

        collected: list[str] = []
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": self._settings.scraper_user_agent},
        ) as client:
            for sitemap_url in sitemap_urls[:3]:
                try:
                    assert_safe_url(sitemap_url)
                    response = await client.get(sitemap_url)
                except Exception as exc:  # noqa: BLE001
                    log.debug("crawler.sitemap_fetch_failed", url=sitemap_url, error=str(exc))
                    continue
                if response.status_code != 200 or len(response.content) > 5_000_000:
                    continue
                result.has_sitemap = True
                urls = parse_sitemap_urls(response.text, base_domain=domain)
                # A sitemap index points at more sitemaps; follow one level only.
                nested = [u for u in urls if u.endswith(".xml")]
                direct = [u for u in urls if not u.endswith(".xml")]
                collected.extend(direct)
                for nested_url in nested[:2]:
                    try:
                        assert_safe_url(nested_url)
                        nested_response = await client.get(nested_url)
                    except Exception:  # noqa: BLE001, S112
                        continue
                    if nested_response.status_code == 200:
                        collected.extend(
                            parse_sitemap_urls(nested_response.text, base_domain=domain)
                        )
                await asyncio.sleep(0)

        result.sitemap_url_count = len(collected)
        return collected[:400]

    async def aclose(self) -> None:
        if self._owns_fetcher:
            await self._fetcher.aclose()
        if self._owns_robots:
            await self._robots.aclose()


async def crawl_site(root_url: str, *, max_pages: int | None = None) -> CrawlResult:
    """Convenience wrapper that owns and closes its own crawler."""
    crawler = Crawler(max_pages=max_pages)
    try:
        return await crawler.crawl(root_url)
    finally:
        await crawler.aclose()


__all__ = ["Crawler", "CrawlResult", "CrawledPage", "CrawlError", "crawl_site"]
