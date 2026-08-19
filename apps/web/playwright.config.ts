import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

/**
 * End-to-end configuration.
 *
 * The suite runs against the real stack: PostgreSQL, Redis, the FastAPI service, a live
 * Celery worker, and a fixture website the real crawler actually crawls over HTTP. The
 * only thing replaced is the internet.
 *
 * That choice is the whole point. A suite that stubs the API proves the UI renders; it
 * proves nothing about whether adding a competitor produces an analysis. This one walks
 * the path a customer walks.
 *
 * Prerequisites: PostgreSQL with pgvector and Redis reachable. `docker compose up -d
 * postgres redis` provides both.
 */

const API_PORT = Number(process.env.E2E_API_PORT ?? 8100);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const FIXTURE_PORT = Number(process.env.E2E_FIXTURE_PORT ?? 4319);

const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;

const API_DIR = path.resolve(__dirname, "../api");

// A dedicated database, dropped and migrated by global setup, so a run never depends on
// or damages development data.
const DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_e2e";
// A separate Redis database keeps the queue clear of anything a developer left behind.
const REDIS_URL = process.env.E2E_REDIS_URL ?? "redis://localhost:6379/1";

export const e2eConfig = {
  API_PORT,
  WEB_PORT,
  FIXTURE_PORT,
  API_URL,
  WEB_URL,
  API_DIR,
  DATABASE_URL,
  REDIS_URL,
  pythonBin:
    process.platform === "win32"
      ? path.resolve(__dirname, "../../.venv/Scripts/python.exe")
      : path.resolve(__dirname, "../../.venv/bin/python"),
};

/** Environment shared by the API and the worker, so they cannot drift apart. */
export const apiEnv: Record<string, string> = {
  ENVIRONMENT: "test",
  SECRET_KEY: "end-to-end-secret-key-that-is-long-enough",
  DATABASE_URL,
  REDIS_URL,
  AI_PROVIDER: "mock",
  // Rate limits would make a fast suite flaky for no coverage gain; quotas, which are the
  // real spend control, stay on.
  RATE_LIMIT_ENABLED: "false",
  CORS_ORIGINS: WEB_URL,
  COOKIE_SECURE: "false",
  LOG_FORMAT: "console",
  LOG_LEVEL: "INFO",
  // The documented test escape hatch, used for exactly its stated purpose: reaching a
  // fixture site on localhost. Production startup refuses to boot with it enabled.
  SCRAPER_ALLOW_PRIVATE_NETWORKS: "true",
  // No politeness delay against our own fixture, but robots.txt is still honoured so the
  // real code path runs.
  SCRAPER_DELAY_SECONDS: "0",
  SCRAPER_RESPECT_ROBOTS: "true",
  SCRAPER_MAX_PAGES: "10",
};

export default defineConfig({
  testDir: "./e2e/specs",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",

  // The suite shares one database and one fixture site whose pricing tests mutate, so
  // parallelism would make runs order-dependent. Sequential and honest beats fast and
  // flaky.
  fullyParallel: false,
  workers: 1,

  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],

  // Generous, because several tests wait on a real crawl and a real worker round trip —
  // and a couple wait on two of them in sequence. A test timeout shorter than the
  // analysis wait inside it fails for reasons that have nothing to do with the product.
  timeout: 240_000,
  expect: { timeout: 15_000 },

  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: [
    {
      command: `node e2e/fixtures/competitor-site.mjs ${FIXTURE_PORT}`,
      url: `http://127.0.0.1:${FIXTURE_PORT}/robots.txt`,
      reuseExistingServer: !process.env.CI,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `"${e2eConfig.pythonBin}" -m uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: API_DIR,
      url: `${API_URL}/health`,
      // Set E2E_REUSE_SERVERS=1 to attach to an API you started yourself — useful when
      // you need to watch its output, and how CI runs it as a service.
      reuseExistingServer: process.env.E2E_REUSE_SERVERS === "1",
      timeout: 120_000,
      env: apiEnv,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // A production build, not `next dev`. The dev server recompiles per route and drops
      // its module cache under a fast-navigating suite, which produces failures that say
      // nothing about the application. This also means the suite exercises the artefact
      // that is actually deployed.
      // The .next directory is removed first: NEXT_PUBLIC_API_URL is inlined into the
      // client bundle at build time, so a stale build points the browser at whatever
      // API the last build was configured for — and the failure is silent, because only
      // client-side requests are affected while server rendering keeps working.
      command: `node -e "require('node:fs').rmSync('.next',{recursive:true,force:true})" && npx next build && npx next start --port ${WEB_PORT}`,
      url: `${WEB_URL}/login`,
      reuseExistingServer: process.env.E2E_REUSE_SERVERS === "1",
      timeout: 300_000,
      env: {
        NEXT_PUBLIC_API_URL: API_URL,
        API_URL,
      },
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
