import { expect, test } from "@playwright/test";

import { PASSWORD, registerAndSignIn, uniqueEmail } from "../helpers";

/**
 * Registration, sign-in, sign-out and recovery.
 *
 * These are the flows where a failure locks a customer out of a product they are paying
 * for, so they are the ones worth walking in a browser rather than asserting through the
 * API.
 */
test.describe("Authentication", () => {
  test("a new user can register and lands on their dashboard", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "register");

    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    // The empty state is the whole page when nothing is tracked: a dashboard of zeros
    // teaches a new user nothing.
    await expect(page.getByRole("heading", { name: "Add your first competitor" })).toBeVisible();
    expect(orgId).toMatch(/^[0-9a-f-]{36}$/);
  });

  test("the session survives a reload and sign-out ends it", async ({ page }) => {
    const { orgId } = await registerAndSignIn(page, "session");

    await page.reload();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

    await page.getByRole("button", { name: /End To End/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/, { waitUntil: "commit" });

    // The dashboard must not be reachable afterwards, cookie cleared or not.
    await page.goto(`/${orgId}`);
    await page.waitForURL(/\/login/, { waitUntil: "commit" });
  });

  test("a wrong password is refused without saying which field was wrong", async ({ page }) => {
    const { email } = await registerAndSignIn(page, "wrongpass");

    await page.getByRole("button", { name: /End To End/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/, { waitUntil: "commit" });

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Incorrect email or password.")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("a short password is rejected before it reaches the server", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Your name").fill("Short Password");
    await page.getByLabel("Work email").fill(uniqueEmail("short"));
    await page.getByLabel("Password").fill("tooshort");
    await page.getByRole("button", { name: "Create workspace" }).click();

    await expect(page.getByText(/at least 12 characters/i)).toBeVisible();
    await expect(page).toHaveURL(/\/register/);
  });

  test("password reset answers the same way for known and unknown addresses", async ({
    page,
  }) => {
    const { email } = await registerAndSignIn(page, "reset");
    await page.getByRole("button", { name: /End To End/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();

    // A real account.
    await page.goto("/forgot-password");
    await page.getByLabel("Email").fill(email);
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

    // An address that does not exist. Anything different here would let an attacker
    // enumerate customers.
    await page.goto("/forgot-password");
    await page.getByLabel("Email").fill(uniqueEmail("nobody"));
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
  });

  test("a reset link without a token explains itself", async ({ page }) => {
    await page.goto("/reset-password");
    await expect(page.getByRole("heading", { name: "Link is incomplete" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Request a new link" })).toBeVisible();
  });

  test("signing in resumes an interrupted invitation", async ({ page }) => {
    const { email } = await registerAndSignIn(page, "next");
    await page.getByRole("button", { name: /End To End/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/, { waitUntil: "commit" });

    await page.goto("/login?next=%2Finvite%3Ftoken%3Dsome-token");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await page.waitForURL(/\/invite\?token=some-token/, { waitUntil: "commit" });
  });

  test("an absolute next target is ignored rather than followed", async ({ page }) => {
    const { email } = await registerAndSignIn(page, "openredirect");
    await page.getByRole("button", { name: /End To End/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/, { waitUntil: "commit" });

    // A mailed sign-in link that bounces the user to an attacker's page is the classic
    // open redirect. Only relative paths are honoured.
    await page.goto("/login?next=https%3A%2F%2Fevil.example.com%2Fphish");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await page.waitForURL(/\/[0-9a-f-]{36}$/, { waitUntil: "commit" });
    expect(page.url()).not.toContain("evil.example.com");
  });
});
