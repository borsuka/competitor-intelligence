import { expect, test } from "@playwright/test";

import { addCompetitor, registerAndSignIn, resetFixture, waitForAnalysis } from "../helpers";

/**
 * Comparison and reporting.
 *
 * Two competitors are needed, so both point at the same fixture server through different
 * loopback addresses. 127.0.0.1 and 127.0.0.2 are distinct domains as far as the unique
 * index is concerned, which keeps the test honest without a second server.
 */
test.describe("Comparison and reports", () => {
  test.beforeEach(async ({ page }) => {
    await resetFixture(page);
  });

  test("two analysed competitors can be compared", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "compare");

    for (const [name, host] of [
      ["Northwind", "127.0.0.1"],
      ["Southgale", "127.0.0.2"],
    ] as const) {
      await addCompetitor(page, orgId, { name, host });
      await page.getByRole("link", { name: new RegExp(name) }).click();
      await page.waitForURL(/\/competitors\/[0-9a-f-]{36}/, { waitUntil: "commit" });
      await waitForAnalysis(page);
    }

    await page.goto(`/${orgId}/compare`);

    await page.getByRole("checkbox").first().check();
    await page.getByRole("checkbox").nth(1).check();
    await page.getByRole("button", { name: "Compare" }).click();

    // --- the matrix -------------------------------------------------------
    await expect(page.getByRole("heading", { name: "Score matrix" })).toBeVisible({
      timeout: 60_000,
    });

    const matrix = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: "Score matrix" }) });
    await expect(matrix.getByRole("cell", { name: "Pricing" })).toBeVisible();
    await expect(matrix.getByRole("cell", { name: "Overall" })).toBeVisible();

    // Sentiment cannot be measured without a review source, and the matrix says so
    // rather than showing a zero that reads as "they are bad at this".
    const sentimentRow = matrix.getByRole("row", { name: /Sentiment/ });
    await expect(sentimentRow.getByText("Insufficient data").first()).toBeVisible();

    // --- the narrative ----------------------------------------------------
    // The scores are deterministic; the narrative is AI and is labelled separately.
    await expect(page.getByRole("heading", { name: "AI comparison" })).toBeVisible();
    await expect(page.getByText("Development provider", { exact: true })).toBeVisible();
  });

  test("comparison explains itself when there is nothing to compare against", async ({
    page,
  }) => {
    const { orgId } = await registerAndSignIn(page, "compare-one");
    await addCompetitor(page, orgId, { name: "Lonely Co" });
    await page.getByRole("link", { name: /Lonely Co/ }).click();
    await waitForAnalysis(page);

    await page.goto(`/${orgId}/compare`);

    // One analysed competitor is not a comparison. Saying so beats rendering a picker
    // that cannot produce a result.
    await expect(
      page.getByRole("heading", { name: "Not enough analysed competitors" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Compare" })).toHaveCount(0);
  });

  test("a competitor overview report is generated from stored data", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "report");
    await addCompetitor(page, orgId, { name: "Reported Co" });
    await page.getByRole("link", { name: /Reported Co/ }).click();
    await waitForAnalysis(page);

    await page.goto(`/${orgId}/reports`);
    await expect(page.getByRole("heading", { name: "No reports yet" })).toBeVisible();

    await page.getByRole("button", { name: "Generate report" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Report type").selectOption("competitor_overview");
    await dialog.getByLabel("Competitor").selectOption({ label: "Reported Co" });
    await dialog.getByRole("button", { name: "Generate" }).click();

    // Generation is synchronous because it only reads stored rows — nothing is
    // re-crawled, so there is no job to wait on.
    await page.waitForURL(/\/reports\/[0-9a-f-]{36}/, { waitUntil: "commit", timeout: 30_000 });

    await expect(page.getByRole("heading", { name: "At a glance" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Competitive score" })).toBeVisible();
    // The report carries the same provenance labelling as the live pages.
    await expect(page.getByText("Observed").first()).toBeVisible();
  });

  test("a weekly intelligence report is generated without selecting a competitor", async ({
    page,
  }) => {
    const { orgId } = await registerAndSignIn(page, "weekly");
    await addCompetitor(page, orgId, { name: "Weekly Co", analyzeNow: false });

    await page.goto(`/${orgId}/reports`);
    await page.getByRole("button", { name: "Generate report" }).first().click();

    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Report type").selectOption("weekly_intelligence");
    await dialog.getByRole("button", { name: "Generate" }).click();

    await page.waitForURL(/\/reports\/[0-9a-f-]{36}/, { waitUntil: "commit", timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "Activity in this period" })).toBeVisible();
    await expect(page.getByText(/No changes were detected/)).toBeVisible();
  });
});
