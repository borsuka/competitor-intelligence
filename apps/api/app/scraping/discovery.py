"""Page discovery: decide which of a site's URLs are worth the crawl budget.

A competitor's site has hundreds of pages and the budget is roughly 25.  Spending it on
the pricing page, the product pages and the about page produces a useful analysis;
spending it on 25 blog posts does not.  This module does that ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.db.models.enums import PageType
from app.scraping.urls import is_probably_binary, normalize_url, same_site

# (page type, base value, url patterns, anchor-text patterns).
# Base value is what one page of this type is worth to an analysis: pricing is the single
# most informative page on a SaaS site, careers pages tell us almost nothing.
_RULES: tuple[tuple[PageType, float, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        PageType.PRICING,
        10.0,
        ("pricing", "price", "plans", "subscribe", "buy", "tarif", "cennik"),
        ("pricing", "plans", "how much"),
    ),
    (
        PageType.PRODUCT,
        8.0,
        ("product", "products", "solutions", "platform", "app", "services"),
        ("product", "solution", "platform"),
    ),
    (
        PageType.FEATURES,
        7.5,
        ("features", "feature", "capabilities", "what-we-do", "how-it-works"),
        ("features", "capabilities", "how it works"),
    ),
    (
        PageType.ABOUT,
        5.5,
        ("about", "about-us", "company", "who-we-are", "story", "team"),
        ("about", "company", "our story"),
    ),
    (
        PageType.CASE_STUDY,
        5.0,
        ("case-study", "case-studies", "customers", "customer-stories", "success"),
        ("case study", "customer", "success story"),
    ),
    (
        PageType.DOCS,
        3.0,
        ("docs", "documentation", "developers", "api", "guides"),
        ("docs", "documentation", "api"),
    ),
    (
        PageType.BLOG,
        2.5,
        ("blog", "news", "insights", "resources", "articles", "press"),
        ("blog", "news", "resources"),
    ),
    (PageType.CONTACT, 2.0, ("contact", "contact-us", "support", "help"), ("contact", "support")),
    (PageType.CAREERS, 0.6, ("careers", "jobs", "hiring", "work-with-us"), ("careers", "jobs")),
    (
        PageType.LEGAL,
        0.2,
        ("privacy", "terms", "legal", "cookie", "gdpr", "imprint", "dpa"),
        ("privacy", "terms", "legal"),
    ),
)

# Query strings and deep paths usually mean a filtered listing or a paginated archive.
_NOISE_PATTERNS = re.compile(
    r"/(tag|tags|category|categories|author|page|archive|search|feed|amp)(/|$)"
    r"|[?&](page|p|q|s|filter|sort)=",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DiscoveredPage:
    url: str
    page_type: PageType
    score: float
    anchor_text: str = ""


def classify_url(url: str, anchor_text: str = "") -> tuple[PageType, float]:
    """Classify a URL and return its discovery value.

    URL path is trusted more than anchor text: anchor text is often decorative
    ("Learn more"), while the path is chosen by the site's own information architecture.
    """
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    anchor = anchor_text.lower().strip()

    if not path:
        return PageType.HOME, 12.0

    segments = [segment for segment in path.split("/") if segment]

    best_type = PageType.OTHER
    best_score = 1.0

    for page_type, base, url_patterns, anchor_patterns in _RULES:
        score = 0.0
        for pattern in url_patterns:
            if segments and segments[0] == pattern:
                score = max(score, base)  # top-level page of that kind: strongest signal
            elif any(segment == pattern for segment in segments):
                score = max(score, base * 0.8)
            elif pattern in path:
                score = max(score, base * 0.5)
        if anchor:
            for pattern in anchor_patterns:
                if anchor == pattern:
                    score = max(score, base * 0.9)
                elif pattern in anchor:
                    score = max(score, base * 0.6)
        if score > best_score:
            best_score = score
            best_type = page_type

    # Depth penalty: /pricing beats /resources/2024/03/how-we-price.
    depth = len(segments)
    if depth > 1:
        best_score *= max(0.35, 1.0 - 0.18 * (depth - 1))

    if _NOISE_PATTERNS.search(url):
        best_score *= 0.3

    return best_type, round(best_score, 3)


def rank_pages(
    links: list[dict[str, str]],
    *,
    base_domain: str,
    limit: int,
    exclude: set[str] | None = None,
) -> list[DiscoveredPage]:
    """Pick the highest-value pages, with per-type caps.

    The caps matter more than the raw ranking: without them a site whose blog dominates
    its internal linking would fill the whole budget with blog posts, and the analysis
    would have no pricing data at all.
    """
    exclude = exclude or set()
    per_type_cap = {
        PageType.BLOG: 3,
        PageType.CASE_STUDY: 3,
        PageType.DOCS: 2,
        PageType.LEGAL: 0,  # legal text is boilerplate; it tells us nothing about strategy
        PageType.CAREERS: 1,
        PageType.PRODUCT: 6,
        PageType.FEATURES: 4,
        PageType.PRICING: 3,
        PageType.OTHER: 4,
    }

    candidates: list[DiscoveredPage] = []
    seen: set[str] = set()

    for link in links:
        url = normalize_url(link.get("url", ""))
        if not url or url in seen or url in exclude:
            continue
        if not same_site(url, base_domain) or is_probably_binary(url):
            continue
        seen.add(url)
        page_type, score = classify_url(url, link.get("text", ""))
        candidates.append(
            DiscoveredPage(
                url=url, page_type=page_type, score=score, anchor_text=link.get("text", "")[:200]
            )
        )

    candidates.sort(key=lambda page: (-page.score, len(page.url)))

    selected: list[DiscoveredPage] = []
    counts: dict[PageType, int] = {}
    for candidate in candidates:
        cap = per_type_cap.get(candidate.page_type, 3)
        if counts.get(candidate.page_type, 0) >= cap:
            continue
        counts[candidate.page_type] = counts.get(candidate.page_type, 0) + 1
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def parse_sitemap_urls(xml: str, *, base_domain: str, limit: int = 500) -> list[str]:
    """Pull ``<loc>`` values out of a sitemap or sitemap index.

    Regex rather than an XML parser on purpose: sitemaps in the wild are frequently
    malformed, and a strict parser turns a useful signal into an exception.  Nothing is
    executed, and every URL is re-validated before it is fetched.
    """
    urls: list[str] = []
    for match in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.IGNORECASE):
        url = match.group(1).strip()
        if not url.startswith(("http://", "https://")):
            continue
        normalized = normalize_url(url)
        if same_site(normalized, base_domain) and not is_probably_binary(normalized):
            urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


__all__ = ["DiscoveredPage", "classify_url", "parse_sitemap_urls", "rank_pages"]
