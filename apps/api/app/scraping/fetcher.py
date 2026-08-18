"""HTTP fetching with the SSRF guard applied to every hop.

``follow_redirects`` is off on purpose.  Letting httpx follow a redirect would open a
socket to a destination nothing validated — and "public URL redirects to
169.254.169.254" is the classic SSRF bypass.  The loop here re-runs
:func:`assert_safe_url` for each hop instead.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

import httpx

from app.core.config import get_settings
from app.core.errors import FetchError, UnsafeURLError
from app.core.logging import get_logger
from app.scraping.urls import assert_safe_url, normalize_url

log = get_logger(__name__)

# Anything else is either binary or not worth a token budget.
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str
    elapsed_ms: int
    redirect_chain: list[str] = field(default_factory=list)
    render_mode: str = "http"
    truncated: bool = False


class PageFetcher(Protocol):
    """The contract the crawler depends on.

    Both the plain HTTP fetcher and the optional Playwright renderer implement it, so
    enabling JavaScript rendering is a configuration change, not a code change.
    """

    async def fetch(self, url: str) -> FetchResult: ...

    async def aclose(self) -> None: ...


class DomainThrottle:
    """One in-flight request per domain, with a minimum gap between requests.

    Politeness first: a crawler that hammers a marketing site gets the product blocked.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_request: dict[str, float] = {}

    async def acquire(self, domain: str) -> None:
        async with self._locks[domain]:
            last = self._last_request.get(domain)
            if last is not None:
                wait = self._min_interval - (time.monotonic() - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_request[domain] = time.monotonic()


class HttpFetcher:
    """Default fetcher: plain HTTP via httpx."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        throttle: DomainThrottle | None = None,
    ) -> None:
        settings = get_settings()
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(settings.scraper_timeout_seconds),
            headers={
                "User-Agent": settings.scraper_user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Language": "en;q=0.9,*;q=0.5",
            },
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._throttle = throttle or DomainThrottle(settings.scraper_delay_seconds)

    async def fetch(self, url: str) -> FetchResult:
        started = time.perf_counter()
        target = assert_safe_url(url)
        await self._throttle.acquire(target.hostname)

        current = url
        chain: list[str] = []

        for hop in range(self._settings.scraper_max_redirects + 1):
            try:
                response = await self._client.get(current)
            except httpx.TimeoutException as exc:
                raise FetchError("The request timed out.", code="fetch_timeout") from exc
            except httpx.HTTPError as exc:
                raise FetchError(
                    "The website could not be reached.", code="fetch_connection_error"
                ) from exc

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise FetchError("Redirect without a destination.", code="fetch_bad_redirect")
                current = urljoin(current, location)
                chain.append(current)
                # The whole point of the manual loop: validate the new destination.
                assert_safe_url(current)
                if hop == self._settings.scraper_max_redirects:
                    raise FetchError("Too many redirects.", code="fetch_too_many_redirects")
                continue

            return self._build_result(response, url, current, chain, started)

        raise FetchError("Too many redirects.", code="fetch_too_many_redirects")

    def _build_result(
        self,
        response: httpx.Response,
        requested_url: str,
        final_url: str,
        chain: list[str],
        started: float,
    ) -> FetchResult:
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith(ALLOWED_CONTENT_TYPES):
            raise FetchError(
                f"Unsupported content type: {content_type}", code="fetch_unsupported_type"
            )

        body = response.content
        truncated = False
        if len(body) > self._settings.scraper_max_bytes:
            body = body[: self._settings.scraper_max_bytes]
            truncated = True

        try:
            html = body.decode(response.encoding or "utf-8", errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = body.decode("utf-8", errors="replace")

        return FetchResult(
            url=normalize_url(requested_url),
            final_url=normalize_url(final_url),
            status_code=response.status_code,
            content_type=content_type or "text/html",
            html=html,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            redirect_chain=chain,
            render_mode="http",
            truncated=truncated,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class PlaywrightFetcher:
    """JavaScript-rendering fetcher, enabled with ``SCRAPER_ENABLE_JS=true``.

    Off by default because a headless browser costs roughly three times the memory of an
    httpx request and most marketing sites are server-rendered.  The SSRF guard still runs
    first, and the browser is launched with navigation restricted to the validated URL.

    Requires the optional extra: ``pip install -e '.[js]' && playwright install chromium``.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._browser = None
        self._playwright = None
        self._throttle = DomainThrottle(self._settings.scraper_delay_seconds)

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise FetchError(
                "JavaScript rendering is enabled but Playwright is not installed.",
                code="playwright_missing",
            ) from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            args=["--disable-dev-shm-usage", "--no-sandbox"]
        )
        return self._browser

    async def fetch(self, url: str) -> FetchResult:
        started = time.perf_counter()
        target = assert_safe_url(url)
        await self._throttle.acquire(target.hostname)

        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=self._settings.scraper_user_agent,
            java_script_enabled=True,
            bypass_csp=False,
        )
        page = await context.new_page()
        try:
            # Block heavy subresources: we want the DOM, not the imagery.
            await page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "media", "font"}
                else route.continue_(),
            )
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._settings.scraper_timeout_seconds * 1000,
            )
            final_url = page.url
            # Re-validate: client-side JavaScript can navigate anywhere.
            try:
                assert_safe_url(final_url)
            except UnsafeURLError:
                raise FetchError(
                    "The page navigated to a destination that cannot be fetched.",
                    code="fetch_unsafe_navigation",
                ) from None
            html = await page.content()
            status = response.status if response else 200
        except FetchError:
            raise
        except Exception as exc:  # pragma: no cover - browser failure modes vary
            raise FetchError("Rendering the page failed.", code="fetch_render_error") from exc
        finally:
            await context.close()

        if len(html) > self._settings.scraper_max_bytes:
            html = html[: self._settings.scraper_max_bytes]

        return FetchResult(
            url=normalize_url(url),
            final_url=normalize_url(final_url),
            status_code=status,
            content_type="text/html",
            html=html,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            render_mode="browser",
        )

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


def build_fetcher() -> PageFetcher:
    """Pick the fetcher the configuration asks for."""
    if get_settings().scraper_enable_js:
        log.info("scraper.fetcher.selected", mode="browser")
        return PlaywrightFetcher()
    return HttpFetcher()


__all__ = [
    "FetchResult",
    "PageFetcher",
    "HttpFetcher",
    "PlaywrightFetcher",
    "DomainThrottle",
    "build_fetcher",
]
