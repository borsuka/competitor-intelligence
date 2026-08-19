import { expect, test } from "@playwright/test";

import {
  addCompetitor,
  registerAndSignIn,
  resetFixture,
  setFixturePrice,
  waitForAnalysis,
} from "../helpers";

/**
 * Change detection and alerting.
 *
 * The fixture site is mutated between two real crawls, so this asserts the product's
 * central claim: that it notices a price move and does not notice anything else.
 */
test.describe("Monitoring", () => {
  test.beforeEach(async ({ page }) => {
    await resetFixture(page);
  });

  test("a price rise is detected, alerted on, and reported with before and after", async ({
    page,
  }) => {
    const { orgId } = await registerAndSignIn(page, "changes");

    // An alert rule first, so the change has something to match when it is detected.
    await page.goto(`/${orgId}/alerts`);
    await page.getByRole("button", { name: "New alert" }).click();
    const ruleDialog = page.getByRole("dialog");
    await ruleDialog.getByLabel("Name").fill("Any pricing move");
    await ruleDialog.getByLabel("Minimum severity").selectOption("low");
    await ruleDialog.getByRole("button", { name: "Create rule" }).click();
    await expect(page.getByText("Any pricing move")).toBeVisible();

    // --- first crawl: the baseline ---------------------------------------
    await addCompetitor(page, orgId, { name: "Northwind Analytics" });
    await page.getByRole("link", { name: /Northwind Analytics/ }).click();
    await page.waitForURL(/\/competitors\/[0-9a-f-]{36}/);
    await waitForAnalysis(page);

    // The first analysis establishes the baseline. Reporting every product and page as
    // "added" would bury the user in false alerts on day one.
    await page.goto(`/${orgId}/monitoring`);
    await expect(page.getByRole("heading", { name: "Nothing has changed" })).toBeVisible();

    // --- the competitor raises its price ---------------------------------
    await setFixturePrice(page, { pro: 79, product: "AI Assistant" });

    await page.goto(`/${orgId}/competitors`);
    await page.getByRole("link", { name: /Northwind Analytics/ }).click();
    await page.getByRole("button", { name: "Refresh analysis" }).click();

    // Wait for the second analysis to land by watching for the change itself.
    await expect(page.getByText(/increased Pro from EUR 49 to EUR 79/i)).toBeVisible({
      timeout: 120_000,
    });

    // --- the monitoring feed ---------------------------------------------
    await page.goto(`/${orgId}/monitoring`);
    await expect(page.getByText(/increased Pro from EUR 49 to EUR 79/i)).toBeVisible();
    // Before and after are shown inline: the diff is the feature, not a detail behind a
    // click.
    await expect(page.getByText("EUR 49").first()).toBeVisible();
    await expect(page.getByText("EUR 79").first()).toBeVisible();

    // A 61% rise is high severity, derived from magnitude rather than from the type.
    await page.getByRole("link", { name: "High only" }).click();
    await expect(page.getByText(/increased Pro from EUR 49 to EUR 79/i)).toBeVisible();

    // --- the notification ------------------------------------------------
    await page.goto(`/${orgId}/alerts`);
    await expect(page.getByText(/increased Pro from EUR 49 to EUR 79/i)).toBeVisible();

    await page.getByRole("button", { name: "Mark all read" }).click();
    await expect(page.getByText(/^\d+ unread$/)).toBeHidden();
  });

  test("an unchanged site produces no noise on a second crawl", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "quiet");

    await addCompetitor(page, orgId, { name: "Quiet Co" });
    await page.getByRole("link", { name: /Quiet Co/ }).click();
    await waitForAnalysis(page);

    // Nothing about the fixture changes between these two crawls.
    await page.getByRole("button", { name: "Refresh analysis" }).click();
    await page.waitForTimeout(3_000);
    await expect(page.getByRole("heading", { name: "Competitive score" })).toBeVisible({
      timeout: 120_000,
    });

    await page.goto(`/${orgId}/monitoring`);
    // A crawler that reports "the homepage changed" every night trains users to ignore
    // it, so silence here is the feature.
    await expect(page.getByRole("heading", { name: "Nothing has changed" })).toBeVisible();
  });

  test("an alert rule can be paused and deleted", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "rules");

    await page.goto(`/${orgId}/alerts`);
    await expect(page.getByRole("heading", { name: "No alert rules" })).toBeVisible();

    await page.getByRole("button", { name: "New alert" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Name").fill("New products only");
    await dialog.getByLabel("Product added").check();
    await dialog.getByRole("button", { name: "Create rule" }).click();

    await expect(page.getByText("New products only")).toBeVisible();
    // Scoped to the saved rule: the same words are also a checkbox label in the dialog.
    const rule = page.locator("li").filter({ hasText: "New products only" });
    await expect(rule.getByText("Product added")).toBeVisible();

    await page.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();

    await page.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();

    await page.getByRole("button", { name: "Delete" }).click();
    await page.getByRole("button", { name: "Confirm" }).click();
    await expect(page.getByRole("heading", { name: "No alert rules" })).toBeVisible();
  });
});
