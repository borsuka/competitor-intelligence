import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const WORKER_PID_FILE = path.resolve(__dirname, ".worker.pid");
const NEXT_ENV_FILE = path.resolve(__dirname, "../next-env.d.ts");

/**
 * The content Next generates for `next-env.d.ts` when building into `.next`.
 *
 * The suite builds into `.next-e2e`, and Next rewrites this reference to whichever
 * directory built last — so without restoring it, running the tests would leave the
 * repository dirty. Next regenerates the file on the next dev or build, so writing the
 * canonical content back is safe.
 */
const CANONICAL_NEXT_ENV = [
  '/// <reference types="next" />',
  '/// <reference types="next/image-types/global" />',
  '/// <reference path="./.next/types/routes.d.ts" />',
  "",
  "// NOTE: This file should not be edited",
  "// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.",
  "",
].join("\n");

function restoreNextEnv(): void {
  if (!fs.existsSync(NEXT_ENV_FILE)) return;
  if (fs.readFileSync(NEXT_ENV_FILE, "utf-8") === CANONICAL_NEXT_ENV) return;
  fs.writeFileSync(NEXT_ENV_FILE, CANONICAL_NEXT_ENV);
}

/**
 * Stops the Celery worker started by global setup, and undoes the one file Next rewrote.
 *
 * Playwright manages the servers it started itself; the worker has no HTTP endpoint to
 * poll, so it is spawned manually and must be reaped here — a leaked worker keeps
 * consuming the queue and holding database connections after the run.
 */
export default async function globalTeardown() {
  restoreNextEnv();

  if (!fs.existsSync(WORKER_PID_FILE)) return;

  const pid = Number(fs.readFileSync(WORKER_PID_FILE, "utf-8").trim());
  fs.rmSync(WORKER_PID_FILE, { force: true });
  if (!pid) return;

  try {
    if (process.platform === "win32") {
      // /T because the worker may have spawned children; without it they survive.
      spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(-pid, "SIGTERM");
    }
    console.log(`[e2e] stopped the Celery worker (pid ${pid})`);
  } catch {
    // Already gone, which is the outcome we wanted anyway.
  }
}
