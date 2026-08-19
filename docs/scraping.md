# Crawling

The user supplies the URL, so this subsystem is a server-side request forgery surface
first and a data source second. Everything else follows from that ordering.

---

## Modules

```
app/scraping/
├── urls.py        normalisation, domain extraction, the SSRF guard
├── fetcher.py     HTTP and optional browser fetchers, per-domain throttle
├── robots.py      robots.txt parsing and caching
├── discovery.py   link classification and crawl-budget ranking
├── extract.py     HTML → observations
└── crawler.py     orchestration
```

The crawler returns plain data and never touches the database, so it is usable from a
test, a script or a worker without a session — and persistence decisions stay in the
service layer where transactions belong.

---

## The SSRF guard

```
url → scheme, port, credentials, hostname shape
    → resolve DNS ourselves, inspect every A/AAAA record
    → reject private, loopback, link-local, CGNAT, reserved, ULA, IPv4-mapped
    → fetch with redirects DISABLED
    → re-validate every hop through the same guard, max 5
    → content-type allow-list, 2 MB cap, 15 s timeout
```

Three decisions worth keeping:

**DNS is resolved here, not trusted.** `evil.example.com` is free to resolve to
`169.254.169.254`. The guard inspects every returned address, and rejects if *any* of them
is private — a rebinding attack that returns one public and one private record must not be
a coin flip.

**`follow_redirects` is off.** Letting the client follow a redirect opens a socket to a
destination nothing validated. "Public URL 302s to the metadata endpoint" is the classic
bypass, and the manual loop exists solely to close it.

**Errors are opaque.** "That host cannot be fetched" for every rejection. A message naming
the resolved address would turn the crawler into an internal network scanner with a
convenient response channel.

A test-only escape hatch, `SCRAPER_ALLOW_PRIVATE_NETWORKS`, disables the IP checks for
local test servers. Production startup refuses to run with it enabled.

**Known gap:** a TOCTOU window between validation and connection. Closing it needs a
transport that pins the connection to the validated IP. See
[security.md](security.md#ssrf).

---

## Politeness

A crawler that hammers a marketing site gets blocked, and a product that ignores site
owners is hard to defend.

| Control | Default |
|---|---|
| robots.txt | Honoured and cached per origin |
| User-agent | Identifies the bot and carries a contact URL |
| Per-domain delay | 1 s, with one in-flight request per domain |
| Pages per analysis | 25 |
| Timeout | 15 s per request |
| Body cap | 2 MB |
| Redirects | 5 hops maximum |
| Fetch order | Sequential — the throttle already serialises per domain |

Only publicly reachable pages are read. Nothing authenticates, and nothing tries to.

---

## Discovery

A competitor's site has hundreds of pages and the budget is 25. Spending it on the pricing
page, the product pages and the about page produces a useful analysis; spending it on 25
blog posts does not.

Each internal link is scored from its URL path and anchor text into a page type
(`pricing`, `product`, `features`, `about`, `case_study`, `docs`, `blog`, `contact`,
`careers`, `legal`), with:

* **A base value per type.** Pricing is the single most informative page on a SaaS site;
  legal boilerplate says nothing about strategy and is capped at zero.
* **A depth penalty.** `/pricing` beats `/resources/2024/03/how-we-price`.
* **A noise penalty** for paginated archives, tag listings and search URLs.
* **Per-type caps**, which matter more than the ranking: without them a site whose blog
  dominates its internal linking fills the whole budget with blog posts and the analysis
  ends up with no pricing data at all.

`robots.txt` sitemap directives and `/sitemap.xml` are read as an additional source, one
index level deep. A missing sitemap is a data point, not an error.

---

## Extraction

Everything `extract.py` produces was present in the fetched markup. It does not infer,
guess or summarise — that is the AI layer's job, and keeping the two apart is what makes
the provenance labelling in the UI truthful.

Collected: title, meta description, canonical, language, Open Graph tags, headings
(h1–h3), JSON-LD blocks, internal and external links, calls to action, prices, and
boilerplate-stripped text.

**Price detection** is deliberately conservative — a false positive becomes a wrong price
in the product, which is worse than a miss. It handles symbol-prefix and code-suffix
forms, both decimal conventions (`1,299.00` and `1.299,00`), and billing periods, and it
keeps both the surrounding context and the text immediately preceding the amount. That
preceding text is what names the plan: with a symmetric window, the first plan on a pricing
page looks like the label for every price on it.

**Boilerplate stripping** removes `nav`, `header`, `footer` and `aside` before hashing.
Chrome repeats on every page: it inflates every hash, dilutes the AI's token budget, and
makes diffs noisy. This is the reason a rotating CSRF token in a footer does not register
as a competitor change.

---

## JavaScript rendering

`httpx` is the default. `PlaywrightFetcher` implements the same `PageFetcher` protocol and
is enabled with `SCRAPER_ENABLE_JS=true`.

Off by default because a headless browser roughly triples memory per fetch and most
marketing sites are server-rendered. When enabled, images, media and fonts are blocked (we
want the DOM, not the imagery) and the final URL is re-validated through the SSRF guard,
because client-side JavaScript can navigate anywhere.

```bash
pip install -e ".[js]"
playwright install chromium
```

---

## Change detection

Detection compares normalised state, not markup — see
[architecture.md](architecture.md#10-change-detection). The relevant crawler contribution
is `text_hash`: a hash of boilerplate-stripped, whitespace-normalised main content. It is
what makes "the page changed" mean something.
