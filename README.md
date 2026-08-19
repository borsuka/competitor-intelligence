# Sentinel

**AI competitor intelligence.** Add a competitor's website URL. Sentinel crawls their
public pages, extracts what they sell and what they charge, scores them, watches for
changes, and tells you which changes matter.

The thing that makes it worth trusting: **every fact carries its source.** A price is
stored as a number only if it was read from a page. An AI interpretation is labelled as
one. A dimension with no supporting data reads "Insufficient data", never zero.

---

## What it does today

| | |
|---|---|
| **Crawl** | SSRF-guarded fetcher, robots.txt honoured, page discovery ranked so a 25-page budget is spent on pricing and product pages |
| **Extract** | Products, pricing plans, features, on-page SEO signals — all traceable to a URL |
| **Analyse** | Staged AI pipeline: extraction → positioning → SWOT → recommendations, every output schema-validated |
| **Score** | Eight dimensions, deterministic, reproducible, with the reasoning stored alongside the number |
| **Compare** | Multi-competitor matrix computed from stored scores, plus an AI narrative that is clearly separated from it |
| **Monitor** | Scheduled re-crawls, structured diffing, severity from magnitude — a 20% price rise, not a changed timestamp |
| **Alert** | Rules per competitor and change type, in-app notifications, webhook and email behind an interface |
| **Report** | Structured documents built from stored data: weekly intelligence, competitor overview, comparison |

Not implemented, and said so plainly: billing, review-data ingestion, ad intelligence,
PDF export, SMTP delivery. See [docs/architecture.md](docs/architecture.md#not-implemented).

---

## Quick start

**Requirements:** Docker, or Python 3.12+ / Node 20+ with PostgreSQL 16 and Redis.

### With Docker

```bash
cp .env.example .env
docker compose up -d
```

The API runs migrations on start. Then open <http://localhost:3000>.

### Without Docker

```bash
# 1. Dependencies
cp .env.example .env
docker compose up -d postgres redis          # or bring your own

# 2. Backend
cd apps/api
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
alembic upgrade head
python scripts/seed.py                       # optional demo data
uvicorn app.main:app --reload --port 8000

# 3. Worker (separate terminal)
celery -A app.workers.celery_app.celery_app worker --loglevel=info -Q analysis,default

# 4. Scheduler (separate terminal, optional in development)
celery -A app.workers.celery_app.celery_app beat --loglevel=info

# 5. Frontend (separate terminal)
cd apps/web && npm install && npm run dev
```

Sign in with the seeded account: `demo@example.com` / `demo-password-for-local-use`.

Full instructions, including Windows notes, are in [docs/development.md](docs/development.md).

---

## About the AI provider

**Sentinel runs without an API key**, using a built-in development provider that derives
structured output from the text the crawler actually collected. It does not interpret,
and it does not invent.

Everything it produces is marked `is_mock` in the database, returned as such by the API,
and displayed as a persistent banner in the UI. It is a development affordance, never a
disguise.

For real analysis, set `ANTHROPIC_API_KEY` and `AI_PROVIDER=anthropic`.

---

## Layout

```
apps/
  api/          FastAPI service + Celery workers (one codebase, three process types)
    app/
      core/       config, logging, security, errors, tenancy
      db/         SQLAlchemy models, session management
      api/v1/     HTTP routers
      services/   business logic — the only layer that writes
      scraping/   SSRF guard, fetcher, discovery, extraction
      ai/         provider abstraction, prompts, output schemas
      workers/    Celery app, tasks, schedule
    alembic/    migrations
    tests/      unit (no dependencies) + integration (needs PostgreSQL)
  web/          Next.js App Router frontend
docker/         container init scripts
docs/           architecture, development, deployment, database, ai, scraping, security
```

---

## Testing

```bash
cd apps/api

pytest tests/unit                                    # no database needed

docker compose up -d postgres
createdb sentinel_test                               # once
TEST_DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_test \
  pytest tests/integration

ruff check . && ruff format --check .
```

```bash
cd apps/web
npm run typecheck && npm run lint && npm run build
```

Integration tests cover the paths that must never regress: tenant isolation, CSRF, quota
enforcement, and the full crawl → analyse → score → diff → alert pipeline.

---

## Documentation

| Document | Contents |
|---|---|
| [architecture.md](docs/architecture.md) | System shape, layering, data model, trade-offs, what is not built |
| [development.md](docs/development.md) | Local setup, workflows, migrations, troubleshooting |
| [deployment.md](docs/deployment.md) | Production checklist, environment, scaling, vendor options |
| [database.md](docs/database.md) | Schema, indexes, migration policy |
| [ai.md](docs/ai.md) | Prompt design, provenance rules, injection defence, cost control |
| [scraping.md](docs/scraping.md) | SSRF guard, politeness, discovery, extraction |
| [security.md](docs/security.md) | Threat model, controls, known limitations |

---

## Licence

No licence has been chosen yet, so all rights are reserved by default. Add one before
sharing this publicly.
