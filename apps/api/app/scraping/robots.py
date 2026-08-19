"""robots.txt handling.

Honouring robots.txt is not legally required for reading public pages, but a crawler
that ignores it gets blocked, and a product that ignores it is hard to defend to the
site owners whose pages it reads.  It is on by default and can only be disabled by
configuration (``SCRAPER_RESPECT_ROBOTS=false``), which is documented as an operator
decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.scraping.urls import assert_safe_url

log = get_logger(__name__)


@dataclass(slots=True)
class RobotsPolicy:
    """Parsed robots.txt for one origin."""

    origin: str
    parser: RobotFileParser | None = None
    sitemaps: list[str] = field(default_factory=list)
    fetched: bool = False
    crawl_delay: float | None = None

    def allows(self, url: str, user_agent: str) -> bool:
        # No robots.txt (or an unreadable one) means no restrictions were expressed.
        if self.parser is None:
            return True
        return self.parser.can_fetch(user_agent, url)


class RobotsCache:
    """Per-crawl cache of robots policies, one entry per origin."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._settings = get_settings()
        self._policies: dict[str, RobotsPolicy] = {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": self._settings.scraper_user_agent},
        )

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def get(self, url: str) -> RobotsPolicy:
        origin = self._origin(url)
        cached = self._policies.get(origin)
        if cached is not None:
            return cached

        policy = RobotsPolicy(origin=origin)
        robots_url = urljoin(origin + "/", "robots.txt")

        try:
            assert_safe_url(robots_url)
            response = await self._client.get(robots_url)
            if response.status_code == 200 and len(response.content) < 512_000:
                body = response.text
                parser = RobotFileParser()
                parser.parse(body.splitlines())
                policy.parser = parser
                policy.fetched = True
                policy.sitemaps = _parse_sitemap_directives(body)
                delay = parser.crawl_delay(self._settings.scraper_user_agent)
                policy.crawl_delay = float(delay) if delay else None
        except Exception as exc:
            log.debug("robots.fetch_failed", origin=origin, error=str(exc))

        self._policies[origin] = policy
        return policy

    async def allows(self, url: str) -> bool:
        if not self._settings.scraper_respect_robots:
            return True
        policy = await self.get(url)
        return policy.allows(url, self._settings.scraper_user_agent)

    async def sitemaps_for(self, url: str) -> list[str]:
        policy = await self.get(url)
        if policy.sitemaps:
            return policy.sitemaps
        return [urljoin(self._origin(url) + "/", "sitemap.xml")]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _parse_sitemap_directives(body: str) -> list[str]:
    sitemaps: list[str] = []
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() == "sitemap":
            candidate = value.strip()
            if candidate.startswith(("http://", "https://")):
                sitemaps.append(candidate)
    return sitemaps[:10]


__all__ = ["RobotsCache", "RobotsPolicy"]
