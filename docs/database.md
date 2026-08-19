# Database

PostgreSQL 16 with `pgvector`. 24 tables. Migrations only — there is no `create_all` in
application code, so development and production reach the same schema by the same path.

---

## Conventions

| Decision | Why |
|---|---|
| UUIDv4 primary keys, generated in Python | An object has an identity before it is flushed, so services can build object graphs without interleaved round trips. Ids are opaque, so they leak no row counts. |
| `organization_id` on every tenant-owned table | A tenant filter never depends on a join being present. |
| Enums as `VARCHAR` + `CHECK`, not native types | Adding a value is a plain migration; `ALTER TYPE` is not, and on older servers cannot run in a transaction. |
| `TIMESTAMPTZ` everywhere, UTC | There is no such thing as a naive timestamp in a product with users in more than one place. |
| Soft delete only on `competitors` and `organizations` | The two things a user can destroy by accident. Everything else cascades, because a table full of tombstones nobody queries is just slower. |
| Explicit constraint naming convention | Alembic autogenerate produces stable, diffable names instead of database-assigned ones. |

---

## Tables

### Identity

| Table | Notes |
|---|---|
| `users` | Email stored lower-cased; the unique index is therefore case-insensitive without needing `citext`. |
| `refresh_tokens` | One row per issued token. `family_id` links a rotation chain so a replayed token can revoke the whole family. |
| `verification_tokens` | Email verification and password reset. Hashed, single use. |
| `organizations` | Tenant root. Carries the user's own company profile, which is what makes recommendations specific. |
| `memberships` | Unique `(organization_id, user_id)`. |
| `invitations` | Hashed token, addressed to an email so it cannot be redeemed by another account. |
| `usage_counters` | Unique `(organization_id, period)`. Quota checks are one indexed row read, not a scan. |
| `audit_logs` | Append-only. No `updated_at`, and nothing in the codebase updates or deletes a row. |

### Competitors and crawling

| Table | Notes |
|---|---|
| `competitors` | Unique `(organization_id, domain)`. Denormalises `last_analyzed_at` and `latest_overall_score` so the list view needs no correlated subquery per row. |
| `competitor_pages` | Unique `(competitor_id, url_hash)`. Uniqueness is on the hash because a btree index cannot cover a 2048-character column. |
| `page_snapshots` | One row per fetch. `content_hash` covers raw HTML, `text_hash` covers normalised text — change detection uses `text_hash`, which is why a rotating CSRF token in the markup is not a change. |
| `seo_snapshots` | Observed on-page signals only. Nothing requiring a third-party rank tool is stored, because it cannot be observed. |

### Analysis

| Table | Notes |
|---|---|
| `analysis_jobs` | The durable state machine. Celery's result backend is deliberately not the source of truth: it expires, is not queryable per organization, and a user needs to see a failure days later. |
| `analyses` | Provider, model, `is_mock`, prompt versions, confidence, token usage, injection flags and the corrections the pipeline applied. |
| `products` / `pricing_plans` | Versioned by `is_current` plus first/last seen, so catalogue history survives without a separate history table. `pricing_plans.amount` is `NUMERIC(12,2)` and null unless a price was parsed from a page. |
| `scores` | Overall plus a JSONB map of dimension → score, weight, inputs and rationale, so the UI can answer "why 82?" from stored data. |
| `embeddings` | `vector(N)` where N is `EMBEDDING_DIMENSIONS` at migration time. Changing it is a migration, not a runtime switch. |

### Monitoring

| Table | Notes |
|---|---|
| `changes` | Type, severity, before/after payloads, magnitude, source URL. |
| `alert_rules` | `competitor_id` null means "every competitor, including ones added later". |
| `notifications` | Written before delivery is attempted, so a failed send is visible rather than lost. |
| `comparisons` | Deterministic `matrix` and AI `insights` in separate columns, because they have different trust levels. |
| `reports` | Structured JSONB documents, not rendered HTML, so the same document can render in-app now and export to PDF later. |

---

## Indexes

Chosen from the queries the application actually issues:

| Index | Query it serves |
|---|---|
| `competitors (organization_id, status)` | The competitor list, which is always filtered by both |
| `competitors (organization_id, created_at)` | Default list ordering |
| `changes (competitor_id, detected_at desc)` | Change history on the detail page |
| `changes (organization_id, detected_at desc)` | The monitoring feed |
| `changes (organization_id, severity)` | Severity filter |
| `page_snapshots (page_id, fetched_at desc)` | `DISTINCT ON` for the newest snapshot per page |
| `analyses (competitor_id, created_at desc)` | Latest analysis, analysis history |
| `analyses (content_fingerprint)` | Skip-if-unchanged lookup before an AI call |
| `embeddings` IVFFlat, `vector_cosine_ops`, lists=100 | Vector search |
| `analysis_jobs (competitor_id, status)` | "Is one already running?" at enqueue |

The IVFFlat `lists=100` suits the low hundreds of thousands of chunks this schema expects.
An order of magnitude more rows and it should be revisited — or replaced with HNSW, which
trades build time and memory for better recall.

---

## Avoiding N+1

The two places it would otherwise appear:

* **Latest snapshot per page** — one `DISTINCT ON (page_id) ORDER BY page_id, fetched_at
  DESC`, not a query per page.
* **Crawl persistence** — pages are loaded once and matched in memory, rather than
  querying per URL. Twenty-five round trips per crawl adds up across a scheduled sweep.

The dashboard aggregates in a fixed number of queries regardless of how many competitors
an organization tracks, using filtered aggregates (`count(...) FILTER (WHERE ...)`) rather
than one query per metric.

---

## Migrations

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Always read the generated file. Autogenerate does not detect index changes reliably, does
not know a type narrowing needs a cast, and cannot know a new non-null column needs a
backfill.

The initial migration creates `vector` with `CREATE EXTENSION IF NOT EXISTS` — a no-op
where an operator or `docker/postgres/init.sql` already created it. On a managed database
that forbids `CREATE EXTENSION`, run it once by hand first.

---

## Growth and retention

`page_snapshots` dominates, holding page text per page per crawl. A 25-page competitor
crawled daily produces roughly 9,000 rows a year, each with up to 200 KB of text.

The retention policy to implement before this matters: keep the rows, null out
`text_content` beyond 90 days. Change detection compares `text_hash`, so history keeps
working while the bulk goes away.
