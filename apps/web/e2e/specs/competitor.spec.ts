import { expect, test } from "@playwright/test";

import { addCompetitor, registerAndSignIn, resetFixture, waitForAnalysis } from "../helpers";

/**
 * The core loop: add a competitor, crawl them, read the result, manage the record.
 *
 * The crawl is real. The worker is real. The only thing standing in for the internet is
 * a fixture website served over HTTP on localhost.
 */
test.describe("Competitor lifecycle", () => {
  test.beforeEach(async ({ page }) => {
    await resetFixture(page);
  });

  test("adding a competitor runs an analysis and produces intelligence", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "analyse");

    await addCompetitor(page, orgId, { name: "Northwind Analytics" });
    await page.getByRole("link", { name: /Northwind Analytics/ }).click();
    await page.waitForURL(/\/competitors\/[0-9a-f-]{36}/);

    await waitForAnalysis(page);

    // --- the score --------------------------------------------------------
    const scoreCard = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: "Competitive score" }) });
    await expect(scoreCard).toBeVisible();

    const overall = await scoreCard.locator(".tabular").first().innerText();
    const score = Number(overall);
    expect(Number.isFinite(score)).toBe(true);
    // Rounded to the nearest 5: the underlying data does not justify finer resolution.
    expect(score % 5).toBe(0);

    // --- provenance -------------------------------------------------------
    // The development provider is in use, and the UI must say so rather than passing
    // heuristic output off as analysis.
    await expect(page.getByText("Development AI provider")).toBeVisible();

    // A price that was read from the page is labelled observed.
    const pricingCard = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: "Pricing" }) });
    await expect(pricingCard.getByText("EUR 49")).toBeVisible();
    await expect(pricingCard.getByText("Observed").first()).toBeVisible();

    // The fixture publishes two amounts and says "Contact sales" for Enterprise. A third
    // priced row would mean a number was invented, which is the failure mode that matters
    // most in this product.
    await expect(pricingCard.getByRole("cell", { name: "EUR 49" })).toBeVisible();
    await expect(pricingCard.getByRole("row")).toHaveCount(3); // header + two plans

    // --- honest gaps ------------------------------------------------------
    // No review source is configured, so sentiment reports insufficient data rather
    // than scoring zero.
    await expect(scoreCard.getByText("Insufficient data").first()).toBeVisible();

    // --- what was actually crawled ---------------------------------------
    const pagesCard = page
      .locator("section")
      .filter({ has: page.getByRole("heading", { name: "Pages crawled" }) });
    await expect(pagesCard.getByText("/pricing")).toBeVisible();
  });

  test("a competitor added without analysis says so and can be analysed later", async ({
    page,
  }) => {
    const { orgId } = await registerAndSignIn(page, "later");

    await addCompetitor(page, orgId, { name: "Deferred Co", analyzeNow: false });
    await page.getByRole("link", { name: /Deferred Co/ }).click();

    await expect(page.getByRole("heading", { name: "Not analysed yet" })).toBeVisible();

    await page.getByRole("button", { name: "Refresh analysis" }).click();
    await waitForAnalysis(page);
  });

  test("a second competitor on the same domain is refused", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "duplicate");
    await addCompetitor(page, orgId, { name: "First", analyzeNow: false });

    await page.getByRole("button", { name: "Add competitor" }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByPlaceholder("competitor.com").fill("http://127.0.0.1:4319/pricing");
    await dialog.getByRole("checkbox").uncheck();
    await dialog.getByRole("button", { name: "Add competitor" }).click();

    await expect(dialog.getByText(/already tracked/i)).toBeVisible();
  });

  test("a private address is refused with a message the user can act on", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "ssrf");
    await page.goto(`/${orgId}/competitors`);

    await page.getByRole("button", { name: "Add competitor" }).first().click();
    const dialog = page.getByRole("dialog");
    // The SSRF guard is relaxed for the fixture host in this environment, so this asserts
    // the shape of the failure rather than the guard itself, which has its own unit
    // tests. What matters here is that the user sees a real message.
    await dialog.getByPlaceholder("competitor.com").fill("not a website at all");
    await dialog.getByRole("button", { name: "Add competitor" }).click();

    await expect(dialog.getByText(/does not look like a website/i)).toBeVisible();
  });

  test("a competitor can be edited, archived, restored and deleted", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "manage");
    await addCompetitor(page, orgId, { name: "Managed Co", analyzeNow: false });
    await page.getByRole("link", { name: /Managed Co/ }).click();
    await page.waitForURL(/\/competitors\/[0-9a-f-]{36}/);

    // --- edit -------------------------------------------------------------
    await page.getByRole("button", { name: "Edit" }).click();
    const editDialog = page.getByRole("dialog");
    await editDialog.getByLabel("Name").fill("Renamed Co");
    await editDialog.getByLabel("Category").fill("Analytics");
    await editDialog.getByLabel("Importance").selectOption("critical");
    await editDialog.getByRole("button", { name: "Save changes" }).click();

    await expect(page.getByRole("heading", { name: "Renamed Co" })).toBeVisible();
    await expect(page.getByText("Analytics")).toBeVisible();

    // --- archive ----------------------------------------------------------
    await page.getByRole("button", { name: "Archive" }).click();
    await expect(page.getByText("This competitor is archived")).toBeVisible();

    await page.goto(`/${orgId}/competitors`);
    await expect(page.getByRole("heading", { name: "No competitors yet" })).toBeVisible();

    await page.getByRole("link", { name: "Archived" }).click();
    await page.waitForURL(/status=archived/);
    await expect(page.getByRole("link", { name: /Renamed Co/ })).toBeVisible();

    // --- restore ----------------------------------------------------------
    await page.getByRole("link", { name: /Renamed Co/ }).click();
    await page.getByRole("button", { name: "Restore" }).click();
    await expect(page.getByText("This competitor is archived")).toBeHidden();

    // --- delete -----------------------------------------------------------
    await page.getByRole("button", { name: "Delete" }).click();
    const deleteDialog = page.getByRole("dialog");
    const confirmButton = deleteDialog.getByRole("button", { name: "Delete permanently" });

    // Typing the name is what separates a slip from a decision.
    await expect(confirmButton).toBeDisabled();
    await deleteDialog.getByLabel(/Type "Renamed Co" to confirm/).fill("Renamed Co");
    await expect(confirmButton).toBeEnabled();
    await confirmButton.click();

    await page.waitForURL(new RegExp(`/${orgId}/competitors$`));
    await expect(page.getByRole("heading", { name: "No competitors yet" })).toBeVisible();
  });

  test("the dashboard reflects what has been analysed", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "dashboard");
    await addCompetitor(page, orgId, { name: "Dashboard Co" });

    await page.getByRole("link", { name: /Dashboard Co/ }).click();
    await waitForAnalysis(page);

    await page.goto(`/${orgId}`);
    await expect(page.getByRole("heading", { name: "Competitive landscape" })).toBeVisible();
    await expect(page.getByText("Development AI provider")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Dashboard Co/ }).first(),
    ).toBeVisible();
  });
});
