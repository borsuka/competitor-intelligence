"""HTML → structured observations.

Everything this module produces is *observed*: it was present in the fetched markup.
Nothing here infers, guesses or summarises — that is the AI layer's job, and keeping the
two apart is what makes the provenance labelling in the UI truthful.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.scraping.urls import is_probably_binary, normalize_url, same_site

# Elements that carry no page-specific meaning.  Removing them before hashing is what
# stops "the cookie banner changed its nonce" from being reported as a competitor change.
BOILERPLATE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "form",
)
CHROME_SELECTORS = ("nav", "header", "footer", "aside")

CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₽": "RUB",
    "лв": "BGN",
    "zł": "PLN",
    "R$": "BRL",
    "C$": "CAD",
    "A$": "AUD",
    "CHF": "CHF",
}
CURRENCY_CODES = frozenset(
    {"USD", "EUR", "GBP", "JPY", "INR", "RUB", "BGN", "PLN", "BRL", "CAD", "AUD", "CHF", "SEK"}
)

# Matches "€49", "$1,299.00", "49 EUR", "USD 19.99/mo".  Deliberately conservative: a
# false positive here becomes a wrong price in the product, which is worse than a miss.
_SYMBOL_ALTERNATION = "|".join(
    re.escape(sym) for sym in sorted(CURRENCY_SYMBOLS, key=len, reverse=True)
)
PRICE_PATTERN = re.compile(
    rf"(?P<pre>{_SYMBOL_ALTERNATION}|\b(?:{'|'.join(CURRENCY_CODES)})\b)\s*"
    r"(?P<amount1>\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?)"
    r"|"
    r"(?P<amount2>\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?)\s*"
    rf"(?P<post>{_SYMBOL_ALTERNATION}|\b(?:{'|'.join(CURRENCY_CODES)})\b)",
    re.IGNORECASE,
)

PERIOD_PATTERN = re.compile(
    r"(?:/|per\s+|a\s+|each\s+)(month|mo\b|year|yr\b|annually|user|seat|day|week)"
    r"|\b(monthly|yearly|annual|annually|one[- ]time|lifetime)\b",
    re.IGNORECASE,
)

CTA_KEYWORDS = (
    "get started",
    "start free",
    "free trial",
    "try free",
    "book a demo",
    "request a demo",
    "schedule a demo",
    "contact sales",
    "talk to sales",
    "sign up",
    "buy now",
    "subscribe",
    "download",
    "get a quote",
)


@dataclass(slots=True)
class ExtractedPage:
    """Structured observations for a single fetched page."""

    url: str
    title: str | None = None
    meta_description: str | None = None
    canonical_url: str | None = None
    lang: str | None = None
    headings: dict[str, list[str]] = field(default_factory=dict)
    open_graph: dict[str, str] = field(default_factory=dict)
    structured_data: list[dict[str, Any]] = field(default_factory=list)
    internal_links: list[dict[str, str]] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    detected_prices: list[dict[str, Any]] = field(default_factory=list)
    calls_to_action: list[str] = field(default_factory=list)
    text_content: str = ""
    word_count: int = 0
    content_hash: str = ""
    text_hash: str = ""

    @property
    def has_h1(self) -> bool:
        return bool(self.headings.get("h1"))


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed or None


def _text_of(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_amount(raw: str) -> float | None:
    """Parse a localised number.

    ``1,299.00`` and ``1.299,00`` both mean the same thing; the separator that appears
    last is the decimal one.  Anything ambiguous returns ``None`` rather than a guess.
    """
    cleaned = raw.strip().replace(" ", "").replace(" ", "")  # noqa: RUF001 - the second is U+00A0, common in prices
    if not cleaned:
        return None

    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")

    if last_dot == -1 and last_comma == -1:
        candidate = cleaned
    elif last_dot > last_comma:
        candidate = cleaned.replace(",", "")
    else:
        candidate = cleaned.replace(".", "").replace(",", ".")

    # A separator followed by exactly three digits was a thousands separator, not a
    # decimal point: "1.299" is one thousand two hundred and ninety-nine.
    if last_dot != -1 and last_comma == -1 and len(cleaned) - last_dot - 1 == 3:
        candidate = cleaned.replace(".", "")
    if last_comma != -1 and last_dot == -1 and len(cleaned) - last_comma - 1 == 3:
        candidate = cleaned.replace(",", "")

    try:
        value = float(candidate)
    except ValueError:
        return None
    if value < 0 or value > 10_000_000:
        return None
    return value


def _currency_of(token: str | None) -> str | None:
    if not token:
        return None
    token = token.strip()
    if token.upper() in CURRENCY_CODES:
        return token.upper()
    return CURRENCY_SYMBOLS.get(token) or CURRENCY_SYMBOLS.get(token.lower())


def detect_prices(text: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """Find price-looking strings with their surrounding context.

    The context is kept so a human (and the AI layer) can see which plan a number
    belonged to, and so the UI can link a price back to the sentence it came from.
    """
    results: list[dict[str, Any]] = []
    seen: set[tuple[float, str | None]] = set()

    for match in PRICE_PATTERN.finditer(text):
        raw_amount = match.group("amount1") or match.group("amount2")
        currency = _currency_of(match.group("pre") or match.group("post"))
        amount = parse_amount(raw_amount) if raw_amount else None
        if amount is None or currency is None:
            continue

        key = (amount, currency)
        if key in seen:
            continue
        seen.add(key)

        start = max(0, match.start() - 90)
        end = min(len(text), match.end() + 90)
        context = text[start:end].strip()
        # The words immediately before a price are what name it, so the left side is
        # kept separately: with a symmetric window, the first plan on a pricing page
        # looks like the label for every price on the page.
        preceding = text[start : match.start()].strip()

        period_match = PERIOD_PATTERN.search(context)
        period = None
        if period_match:
            period = (period_match.group(1) or period_match.group(2) or "").lower()

        results.append(
            {
                "raw": match.group(0).strip(),
                "amount": amount,
                "currency": currency,
                "period": period,
                "context": context,
                "preceding": preceding,
            }
        )
        if len(results) >= limit:
            break
    return results


def _extract_structured_data(soup: BeautifulSoup) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for item in candidates:
            if isinstance(item, dict):
                # Keep the shape, drop the bulk: some sites inline enormous graphs.
                blocks.append(dict(list(item.items())[:30]))
        if len(blocks) >= 20:
            break
    return blocks


def _extract_ctas(soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all(["a", "button"]):
        label = _text_of(node)
        if not label or len(label) > 60:
            continue
        lowered = label.lower()
        if any(keyword in lowered for keyword in CTA_KEYWORDS) and lowered not in seen:
            seen.add(lowered)
            found.append(label)
        if len(found) >= 15:
            break
    return found


def _readable_text(soup: BeautifulSoup) -> str:
    """Main-content text with chrome removed.

    Chrome (nav/header/footer/aside) is stripped because it repeats on every page: it
    inflates every hash, dilutes the AI's token budget and makes diffs noisy.
    """
    working = BeautifulSoup(str(soup), "lxml")
    for selector in (*BOILERPLATE_SELECTORS, *CHROME_SELECTORS):
        for node in working.find_all(selector):
            node.decompose()

    main = working.find("main") or working.find(attrs={"role": "main"}) or working.body or working
    text = main.get_text("\n", strip=True)
    # Collapse runs of blank lines and repeated spaces, so hashing is stable across
    # cosmetic template changes.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_page(html: str, url: str, base_domain: str) -> ExtractedPage:
    """Parse one HTML document into observations."""
    soup = BeautifulSoup(html, "lxml")
    page = ExtractedPage(url=normalize_url(url))

    if soup.title and soup.title.string:
        page.title = _clean_text(soup.title.string)

    description = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if description:
        page.meta_description = _clean_text(description.get("content"))

    canonical = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    if canonical and canonical.get("href"):
        page.canonical_url = normalize_url(urljoin(url, canonical["href"]))

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        page.lang = str(html_tag["lang"])[:16]

    for meta in soup.find_all("meta", attrs={"property": re.compile("^og:", re.I)}):
        content = _clean_text(meta.get("content"))
        if content:
            page.open_graph[str(meta["property"]).lower()] = content[:500]

    for level in ("h1", "h2", "h3"):
        values = [_text_of(node) for node in soup.find_all(level)]
        values = [v for v in values if v][:25]
        if values:
            page.headings[level] = values

    page.structured_data = _extract_structured_data(soup)
    page.calls_to_action = _extract_ctas(soup)

    internal: dict[str, str] = {}
    external: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        if is_probably_binary(absolute):
            continue
        normalized = normalize_url(absolute)
        if same_site(normalized, base_domain):
            if normalized not in internal:
                internal[normalized] = _text_of(anchor)[:200]
        else:
            external.add(normalized)

    page.internal_links = [{"url": u, "text": t} for u, t in list(internal.items())[:400]]
    page.external_links = sorted(external)[:200]

    page.text_content = _readable_text(soup)
    page.word_count = len(page.text_content.split())
    page.detected_prices = detect_prices(page.text_content)

    page.content_hash = hashlib.sha256(html.encode("utf-8", "replace")).hexdigest()
    page.text_hash = hashlib.sha256(page.text_content.encode("utf-8", "replace")).hexdigest()
    return page


__all__ = [
    "CURRENCY_CODES",
    "CURRENCY_SYMBOLS",
    "ExtractedPage",
    "detect_prices",
    "extract_page",
    "parse_amount",
]
