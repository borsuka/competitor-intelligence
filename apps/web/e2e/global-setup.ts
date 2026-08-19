import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { apiEnv, e2eConfig } from "../playwright.config";

/**
 * Prepares the stack before any test runs.
 *
 * 1. Recreates the end-to-end database from migrations. From migrations, not from
 *    `create_all`, because a broken migration chain is a production outage and this is
 *    the cheapest place to catch it.
 * 2. Starts a real Celery worker. Analyses are queued through Redis and executed by a
 *    separate process in production; a suite that bypassed that would leave the most
 *    failure-prone hop untested.
 */

const WORKER_PID_FILE = path.resolve(__dirname, ".worker.pid");

function run(command: string, args: string[], cwd: string, env: Record<string, string>) {
  const result = spawnSync(command, args, {
    cwd,
    env: { ...process.env, ...env },
    encoding: "utf-8",
    shell: false,
  });
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed (${result.status})\n${result.stdout}\n${result.stderr}`,
    );
  }
  return result.stdout;
}

/** Split `postgresql+asyncpg://user:pass@host:port/name` into psycopg-friendly parts. */
function parseDatabaseUrl(url: string) {
  const match = url.match(/^postgresql\+asyncpg:\/\/([^:]+):([^@]+)@([^:/]+):(\d+)\/(.+)$/);
  if (!match) throw new Error(`Could not parse E2E_DATABASE_URL: ${url}`);
  const [, user, password, host, port, name] = match;
  return { user, password, host, port, name };
}

async function resetDatabase() {
  const { user, password, host, port, name } = parseDatabaseUrl(e2eConfig.DATABASE_URL);

  // Dropped and recreated through a maintenance connection, so each run starts from a
  // known empty schema.
  const bootstrap = `
import sys, asyncio, asyncpg

async def main():
    conn = await asyncpg.connect(
        user=${JSON.stringify(user)}, password=${JSON.stringify(password)},
        host=${JSON.stringify(host)}, port=${port}, database="postgres",
    )
    await conn.execute(
        'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
        'WHERE datname = $1 AND pid <> pg_backend_pid()', ${JSON.stringify(name)}
    )
    await conn.execute('DROP DATABASE IF EXISTS "${name}"')
    await conn.execute('CREATE DATABASE "${name}"')
    await conn.close()

    conn = await asyncpg.connect(
        user=${JSON.stringify(user)}, password=${JSON.stringify(password)},
        host=${JSON.stringify(host)}, port=${port}, database=${JSON.stringify(name)},
    )
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.close()

asyncio.run(main())
`;

  run(e2eConfig.pythonBin, ["-c", bootstrap], e2eConfig.API_DIR, apiEnv);
  run(e2eConfig.pythonBin, ["-m", "alembic", "upgrade", "head"], e2eConfig.API_DIR, apiEnv);
}

function startWorker() {
  const args = [
    "-m",
    "celery",
    "-A",
    "app.workers.celery_app.celery_app",
    "worker",
    "--loglevel=warning",
    "-Q",
    "analysis,default",
    "--concurrency=1",
  ];
  // Celery's prefork pool does not work on Windows. The threads pool does, and unlike
  // the solo pool it does not hold a prefetched task while idle — with acks_late that
  // leaves every other job sitting in the queue unexecuted, which looks exactly like a
  // broken product.
  if (process.platform === "win32") args.push("--pool=threads");

  const worker = spawn(e2eConfig.pythonBin, args, {
    cwd: e2eConfig.API_DIR,
    env: { ...process.env, ...apiEnv },
    stdio: "ignore",
    detached: process.platform !== "win32",
  });

  if (!worker.pid) throw new Error("Could not start the Celery worker.");
  fs.writeFileSync(WORKER_PID_FILE, String(worker.pid));
  worker.unref();
  return worker.pid;
}

export default async function globalSetup() {
  console.log("[e2e] resetting the database and applying migrations…");
  await resetDatabase();

  console.log("[e2e] starting the Celery worker…");
  const pid = startWorker();

  // No HTTP endpoint to poll, so give the worker a moment to connect to the broker. The
  // analysis test polls with a generous timeout, so a slow start costs seconds rather
  // than a false failure.
  await new Promise((resolve) => setTimeout(resolve, 5_000));
  console.log(`[e2e] worker running (pid ${pid})`);
}
