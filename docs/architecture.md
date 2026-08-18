# Architecture

**Product:** Sentinel — an AI competitor intelligence platform. A user adds a competitor
website URL; the platform crawls the public site, extracts structured facts, runs a
staged AI analysis over those facts, scores the competitor, tracks changes over time and
raises alerts.

This document describes what is **actually implemented** in this repository. Anything
planned-but-absent is listed explicitly in [Not implemented](#not-implemented).

---

## 1. Shape of the system

A **modular monolith**, not microservices. One Python codebase is deployed as three
process types that share models and services:

```
                       ┌──────────────────────────┐
  browser ──────────▶  │  apps/web (Next.js)      │
                       │  server components + BFF │
                       └────────────┬─────────────┘
                                    │ HTTPS, httpOnly cookies
                       ┌────────────▼─────────────┐
                       │  apps/api (FastAPI)      │  ← process 1: API
                       │  routers → services → db │
                       └───┬────────────────┬─────┘
                           │                │
                   enqueue │                │ read/write
                           ▼                ▼
                    ┌────────────┐   ┌──────────────────┐
                    │   Redis    │   │ PostgreSQL 16    │
                    │ broker +   │   │ + pgvector       │
                    │ cache +    │   └──────────────────┘
                    │ rate limit │            ▲
                    └─────┬──────┘            │
                          │                   │
              ┌───────────▼────────────┐      │
              │ Celery worker          │──────┘  ← process 2: worker
              │ crawl → extract → AI   │
              └───────────┬────────────┘
                          │
              ┌───────────▼────────────┐
              │ Celery beat            │         ← process 3: scheduler
              │ scheduled monitoring   │
              └────────────────────────┘
```

**Why a monolith.** Every "service" here shares the same tenancy model, the same
competitor aggregate and the same transaction boundaries. Splitting them would buy
distributed transactions and no isolation benefit. Boundaries are enforced in-process
by package structure and by the rule that routers never touch the ORM directly.

### Deviation from the suggested layout

The brief suggested a top-level `workers/`. Workers here live in
`apps/api/app/workers/` because they import the same SQLAlchemy models and services;
a separate top-level package would need a shared library and duplicate migrations for
no gain. The *deployment* is still separate (its own container, own scaling).

---

## 2. Backend layering

```
apps/api/app/
├── main.py              ASGI app, middleware, exception handlers, lifespan
├── core/                config, logging, security primitives, errors, limits
├── db/                  engine/session, Base, models/
├── schemas/             Pydantic request/response contracts (API surface)
├── api/v1/              HTTP routers — parse, authorize, delegate, serialize
├── services/            business logic (the only layer allowed to write the DB)
├── scraping/            SSRF guard, fetcher, discovery, extractors
├── ai/                  provider abstraction, prompts, output schemas
└── workers/             Celery app, task definitions, schedules
```

Hard rules, enforced by review and by the import structure:

1. **Routers contain no business logic.** A router resolves the tenant context,
   validates input via Pydantic, calls one service method, returns a schema.
2. **Only services write.** Routers and tasks never call `session.add` / `commit`.
3. **Tasks are thin.** A Celery task opens a session, calls a service, records job
   state. Retry policy lives on the task; logic lives in the service.
4. **The AI layer never touches the DB.** It takes structured input and returns
   validated objects. Persistence is the caller's job — that keeps prompts testable
   offline with no database at all.

### Request lifecycle

```
request
  → RequestContextMiddleware   assigns request_id, binds structlog context
  → SecurityHeadersMiddleware  CSP, nosniff, frame-deny, referrer policy
  → CORS                       credentialed, explicit origin allow-list
  → CSRFMiddleware             double-submit token on unsafe methods
  → RateLimitMiddleware        Redis fixed-window, per-route class
  → router dependency chain    current_user → membership → TenantScope
  → service                    one transaction per request
  → response                   problem+json on error, plain JSON on success
```

---

## 3. Data model

PostgreSQL 16 with the `pgvector` and `citext` extensions. All primary keys are UUIDv4
generated application-side (no round trip, opaque IDs). Every organization-owned table
carries `organization_id` — including tables that could reach the org transitively — so
a tenant filter never depends on a join being present.

### Identity

| table | purpose |
|---|---|
| `users` | credentials, verification state |
| `refresh_tokens` | hashed, rotating, reuse-detected |
| `verification_tokens` | email verification + password reset (hashed, single use) |
| `organizations` | tenant root, plan, quota window |
| `memberships` | user ↔ org with role `owner` / `admin` / `member` / `viewer` |
| `invitations` | pending org invites (hashed token) |

### Competitor intelligence

| table | purpose |
|---|---|
| `competitors` | the tracked entity; unique `(organization_id, domain)` |
| `competitor_pages` | discovered URLs with a classified `page_type` |
| `page_snapshots` | one row per fetch: hashes, text, metadata, structured data |
| `analysis_jobs` | job state machine, progress, attempts, error |
| `analyses` | one AI analysis run: provider, model, prompt version, confidence |
| `products` | extracted products, `is_current` + first/last seen |
| `pricing_plans` | extracted plans with numeric amount + currency, versioned |
| `seo_snapshots` | observed on-page SEO signals per crawl |
| `scores` | overall + per-dimension scores with rationale and inputs |
| `changes` | detected diffs with severity and before/after payloads |
| `comparisons` | multi-competitor comparison results |
| `reports` | generated report documents |
| `alert_rules` / `notifications` | monitoring configuration and delivery log |
| `embeddings` | `vector(N)` chunks for semantic retrieval |
| `usage_counters` | per-org monthly crawl / AI / analysis usage for quotas |
| `audit_logs` | who did what, to which resource, from where |

Soft deletion (`deleted_at`) applies only to `competitors` and `organizations` — the
two things a user can destroy by accident. Everything else cascades.

Indexes follow real query patterns: `(organization_id, status, created_at desc)` on
competitors, `(competitor_id, detected_at desc)` on changes, `(page_id, fetched_at
desc)` on snapshots, plus an IVFFlat index on `embeddings.embedding`.

Migrations are Alembic. There is no `create_all` in application code — the schema is
only ever produced by migrations, so dev and prod follow the same path.

---

## 4. Multi-tenancy and authorization

Tenancy is **explicit, not implicit**. There is no ambient "current org" — the
organization is part of the URL: `/api/v1/orgs/{org_id}/competitors`.

```python
current_user           # decodes access token from httpOnly cookie
  → require_member()   # loads membership for (user, org_id) or 404s
  → require_role(...)  # role check for write operations
  → service(scope)     # every query filters on scope.organization_id
```

Two deliberate choices:

* A membership miss returns **404, not 403** — a 403 confirms the org exists.
* Services take a `TenantScope` value object rather than a bare id, so "which org is
  this query for" is impossible to forget at the call site.

Roles: `owner` (billing, delete org), `admin` (members, competitors, alerts),
`member` (create competitors, run analyses), `viewer` (read-only).

---

## 5. Authentication

* Argon2id password hashing (`passlib`) with a per-deploy pepper from config.
* Short-lived **access JWT** (15 min) + long-lived **refresh JWT** (30 days), both in
  `HttpOnly; Secure; SameSite=Lax` cookies. The frontend never sees a token, so an XSS
  bug cannot exfiltrate credentials.
* Refresh tokens **rotate** on use and are stored as SHA-256 hashes. Presenting an
  already-used refresh token revokes the entire family — the standard detection for a
  stolen token being replayed.
* Because auth is cookie-based, unsafe methods require a **double-submit CSRF token**:
  a readable `csrf_token` cookie echoed in the `X-CSRF-Token` header.
* `app/services/auth.py` separates *credential verification* from *session issuance*,
  so adding an OAuth provider means supplying a verified identity — nothing else.

---

## 6. Crawling

`app/scraping/` is deliberately conservative. The user supplies the URL, so the crawler
is a **server-side request forgery surface first** and a data source second.

```
url → validate_public_url()      scheme, port, hostname shape, no credentials
    → resolve DNS ourselves      all A / AAAA records
    → reject private, reserved, loopback, link-local, metadata ranges
    → fetch with redirects OFF   every hop re-validated through the same guard
    → size cap + timeout + content-type allow-list
    → extract
```

Blocked: non-`http(s)` schemes, credentials in URL, non-standard ports, `localhost`,
RFC1918, `100.64/10`, `169.254/16` (which covers `169.254.169.254`), IPv6 ULA and
loopback, `.internal` / `.local` suffixes. Redirects are followed manually, max 5 hops,
each validated — a public URL that 302s to the metadata endpoint is the classic bypass
and is the reason `follow_redirects` is off.

Politeness: per-domain token bucket, `robots.txt` honoured and cached, configurable
user-agent carrying a contact URL, exponential backoff with jitter, per-analysis page
cap (default 25), 15 s per-request timeout, 2 MB body cap.

Rendering: `httpx` is the default. `PlaywrightRenderer` implements the same
`PageFetcher` protocol and is enabled with `SCRAPER_ENABLE_JS=true`. It is off by
default because a headless browser triples memory and most marketing sites serve
server-rendered HTML.

**Discovery** ranks internal links by URL and anchor heuristics into page types
(`pricing`, `product`, `features`, `about`, `blog`, `case_study`, `contact`) and crawls
the highest-value pages first, so a budget of 25 pages is spent on the pricing page
rather than on 25 blog posts.

---

## 7. AI layer

```
app/ai/
├── base.py          AIProvider protocol: complete_structured() + embed()
├── anthropic.py     real provider (tool-use enforced JSON)
├── mock.py          deterministic development provider — labelled, never silent
├── service.py       AIService: the only thing the rest of the app calls
├── sanitize.py      untrusted-content fencing + injection heuristics
├── schemas.py       Pydantic models every provider response must validate against
└── prompts/         one module per analysis, each with a VERSION constant
```

The pipeline is staged rather than "here is a website, summarise it":

```
snapshots
  → normalise               boilerplate strip, dedupe, per-section token budget
  → structured extraction   products / pricing / features      [AI, JSON schema]
  → validation              Pydantic + numeric sanity checks
  → positioning + SWOT      runs on extracted facts, not raw HTML       [AI]
  → observed SEO signals    pure code, no AI
  → scoring                 deterministic formula over the above
  → embeddings              chunk + vector store
  → comparison / insights   cross-competitor                            [AI]
```

**Provenance is a first-class field.** Every fact carries `source: "observed"` or
`"ai_inference"` plus a `source_url` where one exists, and the UI renders the two
differently. A price is stored as a number only when it was parsed from text on a page;
an AI guess at a price is rejected by validation rather than displayed.

**Prompt injection.** Scraped text is untrusted. It is wrapped in
`<untrusted_content>` fences with a random per-request nonce; the system prompt states
that anything inside the fence is data and can never alter instructions; control
characters and fence lookalikes are stripped; and outputs are schema-validated so even
a "successful" injection cannot produce a field the app will act on. Suspicious content
is flagged on the analysis rather than silently dropped.

**Cost control.** Content is hashed, so an unchanged page is not re-analysed.
Per-section token budgets, a configurable `analysis_depth` (`quick` / `standard` /
`deep`), a model per task class (cheap for extraction, stronger for synthesis), per-org
monthly quotas checked before a job is enqueued, and token usage recorded on every
analysis row.

If no provider key is configured the app uses `MockProvider`, which derives
deterministic structured output from the actually-crawled text. Rows produced this way
carry `provider = 'mock'` and `is_mock = true`, the API returns that flag, and the UI
shows a persistent banner. It is a development affordance, never a disguise.

---

## 8. Scoring

Deterministic and inspectable — `app/services/scoring.py`. Eight dimensions
(`product`, `pricing`, `features`, `positioning`, `seo`, `marketing`, `sentiment`,
`brand`). Each is computed from named inputs, each input contributing a weighted,
bounded sub-score. The stored row keeps those inputs and a rationale string, so the UI
can answer "why 82?" without re-running anything.

Scores are reported in steps of 5 alongside an explicit `confidence` and a
`data_completeness` ratio. A dimension with no supporting data is `null` —
"Insufficient data" — and is excluded from the weighted mean rather than defaulting to
zero. False precision is a trust bug.

---

## 9. Jobs

Celery over Redis. `analysis_jobs` is the durable state machine (`pending → running →
completed | failed | cancelled`) with `progress` (0–100), `attempts`, `error` and
timings. The Celery task id is stored so a job can be revoked.

Idempotency: a job is keyed by `(competitor_id, job_type, content_fingerprint)`. A
re-run against unchanged content short-circuits to the previous analysis instead of
paying for the AI call again. Retries are exponential with jitter, capped at 3, and
only for transient classes (network, 5xx, provider rate limit) — a validation failure
is terminal and surfaces to the user.

`celery beat` sweeps competitors whose `monitoring_interval` has elapsed and enqueues
refresh analyses, spread across the window so 500 competitors do not stampede.

---

## 10. Change detection

Meaningful diffs, not HTML noise.

* Each snapshot stores a **text hash** of normalised, boilerplate-stripped content, so
  a rotating CSRF token or a changed timestamp does not register as a change.
* Structured diffs run over extracted entities: pricing amounts, product set, feature
  set, positioning statement, page inventory.
* Every change gets a `type` and a `severity` derived from magnitude — a 20% price rise
  is `high`, a new blog post is `low` — plus before/after payloads for the UI.
* Only changes at or above an alert rule's `min_severity` produce a notification.

---

## 11. Frontend

Next.js App Router, TypeScript strict, Tailwind + a customised shadcn/ui layer.

* **Server components by default.** Lists and detail pages fetch server-side with the
  request's cookies forwarded, so first paint has data and there is no loading
  waterfall.
* **Client components only where interaction demands it**: forms (React Hook Form +
  Zod, sharing the API's contracts), charts (Recharts), job-status polling, comparison
  selection, command palette.
* **TanStack Query** is used exclusively for interactive and polling surfaces; static
  reads stay on the server.
* Mutations go through Next route handlers acting as a thin **BFF**, which attach the
  CSRF header and keep the API origin out of the browser.
* Every route ships `loading.tsx` with skeletons matching the final layout, an
  `error.tsx`, and an explicit empty state.

Design system in `apps/web/src/app/globals.css` and `src/components/ui`: one type
scale, a 4px spacing grid, two radii, two shadow levels, a restrained neutral palette
with a single accent, and semantic status colours. Dark mode via CSS custom properties.

---

## 12. Security summary

| surface | control |
|---|---|
| passwords | Argon2id + pepper |
| sessions | HttpOnly/Secure/SameSite cookies, rotating refresh, reuse detection |
| CSRF | double-submit token on all unsafe methods |
| tenancy | org id in path, membership dependency, `TenantScope` in every query |
| SSRF | DNS-level IP guard, per-hop redirect validation, port/scheme allow-list |
| SQL injection | SQLAlchemy bound parameters only; no string-built SQL |
| XSS | React escaping, no `dangerouslySetInnerHTML` on scraped text, strict CSP |
| prompt injection | fenced untrusted content, schema-validated output |
| abuse | Redis rate limits per route class, per-org quotas on crawl and AI |
| secrets | env only, `.env` git-ignored, startup fails on default secrets in prod |
| logging | structured JSON with request/org id; token, password and key fields redacted |

---

## 13. Observability

`structlog` JSON to stdout. Every line carries `request_id` (or `task_id`),
`organization_id`, and where relevant `competitor_id`, `job_id` and `duration_ms`.
Sentry and OpenTelemetry are wired behind config flags — enabled by setting a DSN or
endpoint, with no code change. `/health` (liveness) and `/health/ready` (DB + Redis)
are deliberately separate.

---

## 14. Deployment

Docker Compose for local development: `postgres`, `redis`, `api`, `worker`, `beat`,
`web`. Multi-stage builds, non-root users, slim runtime layers. Nothing is
vendor-specific: the API needs a Postgres URL and a Redis URL, the web app needs an API
URL. The frontend runs anywhere Node runs (Vercel or the container); the backend runs
anywhere a container runs.

---

## Not implemented

Stated plainly, so these docs describe reality:

* **Billing.** `organizations.plan` and quotas exist and are enforced; there is no
  payment provider integration.
* **Review data.** The `ReviewProvider` interface and sentiment analysis exist, but no
  review source is configured, so sentiment reports "Insufficient data". No review data
  is fabricated to fill the gap.
* **Ad intelligence.** Ad libraries require credentialed APIs; the schema and interface
  are absent rather than faked.
* **PDF export.** Reports are generated as structured documents rendered in-app; the
  export interface exists, the PDF renderer does not.
* **Email delivery.** Notifications are persisted and rendered in-app; the SMTP channel
  sits behind an interface with a console implementation for development.
