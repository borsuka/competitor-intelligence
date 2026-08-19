# Development

Everything needed to get a working local environment and know where things live.

---

## Requirements

* **Python 3.12+** — the codebase uses PEP 695 generics
* **Node 20+**
* **Docker** — for PostgreSQL 16 with pgvector, and Redis
* **PostgreSQL 16 with the `vector` extension** if you prefer not to use Docker

---

## Setup

### 1. Configuration

```bash
cp .env.example .env
```

The defaults work for local development as-is. The only value worth changing immediately
is `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2. Data services

```bash
docker compose up -d postgres redis
```

`docker/postgres/init.sql` creates the `vector` and `pg_trgm` extensions on first boot.
If you are pointing at your own PostgreSQL, run that file once by hand.

### 3. Backend

```bash
cd apps/api
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip
alembic upgrade head
```

### 4. Demo data (optional, recommended)

```bash
python scripts/seed.py
```

Creates `demo@example.com` / `demo-password-for-local-use` with three competitors,
analyses, scores and a change. Every seeded competitor is prefixed `[DEMO]` and uses an
RFC 2606 reserved domain, so demo records can never be confused with real intelligence.
Re-run with `--reset` to start clean.

### 5. Run the three processes

```bash
# API
uvicorn app.main:app --reload --port 8000

# Worker
celery -A app.workers.celery_app.celery_app worker --loglevel=info -Q analysis,default

# Scheduler — only needed if you want automatic re-crawls
celery -A app.workers.celery_app.celery_app beat --loglevel=info
```

### 6. Frontend

```bash
cd apps/web
npm install
npm run dev
```

<http://localhost:3000>. API docs at <http://localhost:8000/docs>.

---

## Windows notes

* Celery's prefork pool does not work on Windows. Use `--pool=solo`:

  ```bash
  celery -A app.workers.celery_app.celery_app worker --pool=solo -Q analysis,default
  ```

  This is a Windows development limitation only; the Docker worker runs on Linux with the
  normal pool.

* Use `.venv\Scripts\python` rather than `.venv/bin/python`.

---

## Project layout

```
apps/api/app/
├── main.py          ASGI app: middleware, exception handlers, lifespan
├── core/            config, logging, security primitives, errors, tenancy
├── db/              engine and session, Base, models/
├── schemas/         Pydantic request/response contracts
├── api/v1/          routers — parse, authorize, delegate, serialize
├── services/        business logic; the only layer that writes to the database
├── scraping/        SSRF guard, fetcher, discovery, extraction
├── ai/              provider abstraction, prompts, output schemas
└── workers/         Celery app, tasks, schedule
```

Four rules keep the layering honest:

1. Routers contain no business logic.
2. Only services write. Routers and tasks never call `session.add` or `commit`.
3. Tasks are thin: open a session, call a service, record job state.
4. The AI layer never touches the database, so prompts are testable with no database at all.

---

## Common tasks

### Add a database column

```bash
cd apps/api
# 1. edit the model in app/db/models/
alembic revision --autogenerate -m "add competitor.industry"
# 2. READ the generated migration — autogenerate misses index and type changes
alembic upgrade head
```

There is no `create_all` in application code. The schema is only ever produced by
migrations, so development and production follow the same path.

### Add an API endpoint

1. Request/response models in `app/schemas/`.
2. Business logic in `app/services/`, taking a `TenantScope` first argument.
3. A router in `app/api/v1/` that resolves the scope, calls one service method, returns a
   schema.
4. A test in `tests/integration/` if it touches tenancy, quotas or money.

### Change a prompt

Edit the module in `app/ai/prompts/` and **bump its `VERSION`**. The version is stored on
every analysis, which is what makes a change in output quality attributable later.

### Run one thing

```bash
pytest tests/unit/test_scoring.py -v
pytest -k "tenant_isolation"
ruff check app/services/analysis.py
```

---

## Testing

```bash
cd apps/api

# Unit — pure logic, no database, no network. Fast.
pytest tests/unit

# Integration — needs PostgreSQL with pgvector.
docker compose up -d postgres
docker exec sentinel-postgres-1 psql -U sentinel -d postgres -c "CREATE DATABASE sentinel_test"
TEST_DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_test \
  pytest tests/integration
```

Integration tests are skipped, not failed, when `TEST_DATABASE_URL` is unset.

They replace the crawler with a synthetic two-page site, so they are hermetic: no network,
no rate limits, no dependence on a third party's markup. What they do exercise for real is
the database, the API, the tenancy checks and the whole pipeline.

Frontend:

```bash
cd apps/web
npm run typecheck
npm run lint
npm run build
```

---

## Working without an AI key

The default `AI_PROVIDER=mock` runs a development provider that derives structured output
from crawled text using deterministic heuristics. It is not an AI and does not pretend to
be: everything it produces is flagged `is_mock` and shows a banner in the UI.

Vector search behaves similarly — without `VOYAGE_API_KEY` it uses a local lexical hashing
embedder, and the search API returns `is_lexical: true` so results are never presented as
semantic.

To use the real provider:

```bash
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Troubleshooting

**`relation "competitors" does not exist`** — migrations have not run. `alembic upgrade head`.

**`type "vector" does not exist`** — the pgvector extension is missing.
`CREATE EXTENSION vector;` as a superuser.

**Analyses stay `pending`** — no worker is consuming the queue. Start one, and check that
`REDIS_URL` matches between the API and the worker.

**Every crawl fails with `unsafe_url`** — expected for `localhost`, private ranges and
reserved TLDs (`.test`, `.local`, `.example`). That is the SSRF guard working. For a test
server, set `SCRAPER_ALLOW_PRIVATE_NETWORKS=true` in development only; production refuses
to start with it enabled.

**Frontend shows "Could not reach the server"** — check `NEXT_PUBLIC_API_URL`, and that
the API's `CORS_ORIGINS` includes the web origin. Credentialed CORS requires an exact
origin match; `*` will not work.

**Everything is 403 `csrf_failed`** — the client is not echoing the `sentinel_csrf` cookie
in `X-CSRF-Token`. `clientFetch` does this automatically; a manual `fetch` will not.

---

## Code style

* Python: `ruff check` and `ruff format`, 100-column lines.
* TypeScript: strict mode, `noUncheckedIndexedAccess` on.
* Comments explain *why*, not *what*. A comment restating the code is noise; a comment
  explaining a non-obvious trade-off is the reason the next person does not undo it.
