import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const WORKER_PID_FILE = path.resolve(__dirname, ".worker.pid");

/**
 * Stops the Celery worker started by global setup.
 *
 * Playwright manages the servers it started itself; the worker has no HTTP endpoint to
 * poll, so it is spawned manually and must be reaped here — a leaked worker keeps
 * consuming the queue and holding database connections after the run.
 */
export default async function globalTeardown() {
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
