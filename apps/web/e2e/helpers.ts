import { expect, type Page } from "@playwright/test";

import { e2eConfig } from "../playwright.config";

/** A fresh address per test, so runs never collide on the unique email index. */
export function uniqueEmail(prefix = "e2e"): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
}

export const PASSWORD = "end-to-end-passphrase-1";

export interface Account {
  email: string;
  orgId: string;
}

/**
 * Registers through the UI and lands on the dashboard.
 *
 * Through the UI rather than the API on purpose: registration is one of the flows the
 * brief lists, so every test that needs an account exercises it.
 */
export async function registerAndSignIn(page: Page, prefix = "e2e"): Promise<Account> {
  const email = uniqueEmail(prefix);

  await page.goto("/register");
  await page.getByLabel("Your name").fill("End To End");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel("Company or team name").fill("E2E Workspace");
  await page.getByRole("button", { name: "Create workspace" }).click();

  // Registration redirects to /{orgId}; waiting for the URL is what proves it worked.
  await page.waitForURL(/\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  const orgId = new URL(page.url()).pathname.slice(1);

  return { email, orgId };
}

/** Adds a competitor pointing at the fixture site. */
export async function addCompetitor(
  page: Page,
  orgId: string,
  {
    name = "Northwind Analytics",
    analyzeNow = true,
    host = "127.0.0.1",
  }: { name?: string; analyzeNow?: boolean; host?: string } = {},
): Promise<void> {
  await page.goto(`/${orgId}/competitors`);
  await page.getByRole("button", { name: "Add competitor" }).first().click();

  const dialog = page.getByRole("dialog");
  await dialog.getByPlaceholder("competitor.com").fill(fixtureUrl("", host));
  await dialog.getByLabel("Name").fill(name);

  const analyseToggle = dialog.getByRole("checkbox");
  if (analyzeNow) {
    await analyseToggle.check();
  } else {
    await analyseToggle.uncheck();
  }

  await dialog.getByRole("button", { name: "Add competitor" }).click();
  await expect(page.getByRole("link", { name: new RegExp(name, "i") })).toBeVisible({
    timeout: 20_000,
  });
}

/**
 * The fixture site's URL.
 *
 * `host` exists so two competitors can point at the same server without colliding on the
 * unique (organization, domain) index. 127.0.0.1 and 127.0.0.2 are distinct hostnames and
 * both reach the fixture, which binds the wildcard. Not "localhost": it has no dot, and
 * single-label hostnames are refused as not-a-website.
 */
export function fixtureUrl(path = "", host = "127.0.0.1"): string {
  return `http://${host}:${e2eConfig.FIXTURE_PORT}${path}`;
}

/** Change the fixture site between crawls, so the next analysis has something to detect. */
export async function setFixturePrice(
  page: Page,
  { pro, product }: { pro?: number; product?: string },
): Promise<void> {
  const url = new URL(fixtureUrl("/__set-price"));
  if (pro !== undefined) url.searchParams.set("pro", String(pro));
  if (product !== undefined) url.searchParams.set("product", product);

  const response = await page.request.get(url.toString());
  expect(response.ok()).toBeTruthy();
}

export async function resetFixture(page: Page): Promise<void> {
  const response = await page.request.get(fixtureUrl("/__reset"));
  expect(response.ok()).toBeTruthy();
}

/**
 * Waits for the analysis a competitor page is running to finish.
 *
 * The crawl is real — five pages over HTTP — and the job travels through Redis to a
 * separate worker process, so this is generous. The page polls the job itself and
 * refreshes when it completes; waiting for the score to appear is the user-visible
 * definition of "done".
 */
export async function waitForAnalysis(page: Page, timeout = 120_000): Promise<void> {
  await expect(page.getByRole("heading", { name: "Competitive score" })).toBeVisible({
    timeout,
  });
}


/**
 * Waits for a re-analysis triggered from the detail page to finish.
 *
 * Watching for the score card is not enough on a competitor that has been analysed
 * before: the card is already on screen, so the assertion passes immediately and the next
 * navigation races the reload the progress panel fires on completion. The refresh button
 * is disabled for exactly as long as a job is running, which is the signal that means
 * what it says.
 */
export async function waitForReanalysis(page: Page, timeout = 120_000): Promise<void> {
  const button = page.getByRole("button", { name: "Refresh analysis" });
  await expect(button).toBeDisabled({ timeout: 30_000 });
  await expect(button).toBeEnabled({ timeout });
}
