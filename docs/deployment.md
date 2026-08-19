# Deployment

Nothing here is vendor-specific. The API needs a PostgreSQL URL and a Redis URL; the web
app needs an API URL. Everything else is a choice.

---

## Shape

Three process types from one image, plus two managed services:

| Process | Command | Scaling signal |
|---|---|---|
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers` | request latency |
| Worker | `celery -A app.workers.celery_app.celery_app worker -Q analysis,default` | queue depth |
| Scheduler | `celery -A app.workers.celery_app.celery_app beat` | **exactly one instance** |
| Frontend | `next start` | request latency |
| PostgreSQL 16 + pgvector | managed | connections, storage |
| Redis 7 | managed | memory |

Run **one** beat instance. Two schedulers double every scheduled crawl and every AI call
that follows it.

---

## Before the first deploy

```
[ ] SECRET_KEY set to 32+ random characters (never the .env.example default)
[ ] PASSWORD_PEPPER set — and understood: changing it invalidates every password
[ ] COOKIE_SECURE=true
[ ] DEBUG=false, ENVIRONMENT=production
[ ] CORS_ORIGINS lists the exact web origin (credentialed CORS rejects "*")
[ ] SCRAPER_ALLOW_PRIVATE_NETWORKS=false
[ ] LOG_FORMAT=json
[ ] CREATE EXTENSION vector; run once on the managed database
[ ] alembic upgrade head
[ ] TLS terminated in front of the API, with the proxy overwriting X-Forwarded-For
```

`Settings.validate_production` refuses to start if the first five are wrong. That is
deliberate: a service that boots with a default signing key is worse than one that does
not boot.

---

## Environment

Everything is in [`.env.example`](../.env.example) with comments. The values that matter
in production:

| Variable | Note |
|---|---|
| `SECRET_KEY` | Signs session JWTs. Rotating it signs everyone out. |
| `PASSWORD_PEPPER` | Kept outside the database so a dump alone cannot be cracked offline. |
| `DATABASE_URL` | `postgresql+asyncpg://…`. The `+asyncpg` driver is required. |
| `REDIS_URL` | Broker, cache and rate limiter. |
| `ANTHROPIC_API_KEY` | Without it the app runs the labelled development provider. |
| `CORS_ORIGINS` | Exact origins, comma separated. |
| `COOKIE_DOMAIN` | Set when the API and web app share a parent domain. |

---

## Cookies across subdomains

Sessions are cookies, so the API and the frontend must be same-site.

* `app.example.com` + `api.example.com` → set `COOKIE_DOMAIN=.example.com`. `SameSite=Lax`
  works, because these are the same site.
* Different registrable domains → cookies will not be sent. Either move the API onto a
  subdomain, or put it behind the same domain at a path prefix.

Do not reach for `SameSite=None` to work around this. It weakens CSRF protection to solve
a problem that a DNS record solves properly.

---

## Migrations

```bash
alembic upgrade head
```

Run it as a release step, before new code serves traffic. The compose file does this in
the API container's start command so a failed migration stops the container rather than
serving against an old schema.

Two rules:

* **Never assume the database can be recreated.** Every schema change is a migration.
* **Read what autogenerate produces.** It misses index changes, type narrowing and
  constraint renames, and it cannot know that a column needs backfilling.

For a column that must be non-null: add it nullable, backfill, then add the constraint in
a second migration. A single-step migration on a large table takes a lock nobody wants.

---

## Scaling

**API** — stateless. Scale horizontally. Size the connection pool so
`instances × (pool_size + max_overflow)` stays under the database's connection limit; a
managed Postgres with a 100-connection cap and the default `10 + 20` supports roughly
three instances before a pooler is needed.

**Worker** — the bottleneck is crawling, which is IO-bound and deliberately throttled
per domain. Concurrency 2–4 per container. Watch queue depth on the `analysis` queue.

**Redis** — set `maxmemory-policy allkeys-lru`. It holds the broker, the cache and rate
limit counters; an unbounded cache is an outage waiting for a busy week.

**PostgreSQL** — `page_snapshots` grows fastest, holding page text per fetch. Plan a
retention policy: keep snapshot rows but drop `text_content` beyond 90 days, which keeps
change detection working (it compares hashes) while removing most of the volume.

---

## Where it can run

| Component | Options |
|---|---|
| Frontend | Vercel, or the container anywhere Node runs |
| API and workers | Any container host — Fly.io, Railway, Render, ECS, Cloud Run |
| PostgreSQL | Anything with pgvector: RDS, Cloud SQL, Neon, Supabase, Crunchy |
| Redis | ElastiCache, Upstash, Redis Cloud, or a container |

The only real constraint is pgvector on the database, and one persistent scheduler
process. Cloud Run and similar scale-to-zero platforms suit the API but not `beat`.

---

## Observability

Structured JSON to stdout with `LOG_FORMAT=json`. Every line carries `request_id` or
`task_id`, `organization_id`, and where relevant `competitor_id`, `job_id` and
`duration_ms`.

Health endpoints are separate on purpose:

* `/health` — liveness. Touches nothing. Use this for the restart probe: restarting a pod
  because Redis blipped makes an outage worse.
* `/health/ready` — checks PostgreSQL and Redis, returns 503 when either is down. Use this
  for the load balancer.

Sentry and OpenTelemetry are enabled by `SENTRY_DSN` and `OTEL_EXPORTER_OTLP_ENDPOINT`.
They are optional dependencies, so the production image needs the extra:

```dockerfile
RUN pip install -e ".[observability]"
```

Setting a DSN without installing the extra logs a warning at startup rather than passing
silently. Sentry is configured with `send_default_pii=False` and a scrubbing `before_send`
— this product holds competitor data on behalf of tenants, and shipping request bodies to
a third party is not a default worth having.

Worth alerting on: `analysis_jobs` stuck in `running` past 45 minutes (the sweeper handles
it, but a spike means something else is wrong), quota rejections rising, and
`ai.pricing_corrections` climbing, which means the model has started inventing prices.

---

## Backups

PostgreSQL is the only durable store. Redis holds a queue and a cache; losing it drops
in-flight jobs, which the stuck-job sweeper re-queues.

Take point-in-time recovery on the database and **test a restore**. A backup nobody has
restored is a hypothesis.

---

## Rollback

Code rolls back by redeploying the previous image. Migrations mostly do not — Alembic
`downgrade` exists but a migration that dropped a column cannot bring the data back.

Prefer forward-compatible migrations: add before removing, deploy, remove in a later
release once nothing reads the old column.
