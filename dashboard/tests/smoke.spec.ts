import { expect, test, type Page } from "@playwright/test";

declare const process: {
  env: Record<string, string | undefined>;
};

const adminUser = process.env.PLAYWRIGHT_ADMIN_USER ?? "admin";
const adminPassword = process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? "admin";
const scopedUser = process.env.PLAYWRIGHT_SCOPED_USER ?? "alpha-user";
const scopedPassword = process.env.PLAYWRIGHT_SCOPED_PASSWORD ?? "alpha-user";
const scopedSchool =
  process.env.PLAYWRIGHT_SCOPED_SCHOOL ?? "Beahanberg High School";

async function login(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign In" }).click();
}

test("admin can log in, run a query, and use the admin screen", async ({
  page,
}) => {
  await login(page, adminUser, adminPassword);

  await expect(
    page.getByRole("heading", { name: "Explore Data" }),
  ).toBeVisible();

  // Admin defaults to whichever school comes first alphabetically, which may
  // have no seeded data - select the school we actually seeded explicitly.
  await page.getByLabel("School", { exact: true }).selectOption({ label: scopedSchool });

  // The app auto-selects a default variable on load (whichever sorts first
  // alphabetically), which may belong to a different form version than the
  // one we're about to select - querying both together trips the
  // incompatible-versions suppression guard. Clear it first.
  for (const box of await page.getByRole("checkbox", { checked: true }).all()) {
    await box.uncheck();
  }

  // Test dashboard query functionality - phq9_questionnaire__phq9_1 is
  // guaranteed to have data; other variables (e.g. bw_wbeing_1) exist in the
  // form but were never seeded. Target the full namespaced key: bewell also
  // has a raw_key "phq9_1" (namespace-collision fixture), so a loose
  // /phq9_1/ match resolves to two checkboxes.
  await page
    .getByRole("checkbox", { name: /\[phq9_questionnaire__phq9_1\]$/ })
    .check();
  await expect(page.getByRole("button", { name: "Run Query" })).toBeEnabled();
  await page.getByRole("button", { name: "Run Query" }).click();

  // Wait for query results to appear - ChartCard renders a <canvas> via
  // svelte-chartjs, there is no ".chart-container" class in the app.
  await expect(page.locator("canvas")).toBeVisible({
    timeout: 10000,
  });

  // Test admin screen
  await page.goto("/admin");
  await expect(
    page.getByRole("heading", { name: "User Management" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: adminUser, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: scopedUser, exact: true }),
  ).toBeVisible();
});

test("scoped user can log in and run queries", async ({ page }) => {
  await login(page, scopedUser, scopedPassword);

  await expect(
    page.getByRole("heading", { name: "Explore Data" }),
  ).toBeVisible();

  // The app auto-selects a default variable on load (whichever sorts first
  // alphabetically), which may belong to a different form version than the
  // one we're about to select - querying both together trips the
  // incompatible-versions suppression guard. Clear it first.
  for (const box of await page.getByRole("checkbox", { checked: true }).all()) {
    await box.uncheck();
  }

  // Test dashboard query functionality - phq9_questionnaire__phq9_1 is
  // guaranteed to have data; other variables (e.g. bw_wbeing_1) exist in the
  // form but were never seeded. Target the full namespaced key: bewell also
  // has a raw_key "phq9_1" (namespace-collision fixture), so a loose
  // /phq9_1/ match resolves to two checkboxes.
  await page
    .getByRole("checkbox", { name: /\[phq9_questionnaire__phq9_1\]$/ })
    .check();
  await expect(page.getByRole("button", { name: "Run Query" })).toBeEnabled();
  await page.getByRole("button", { name: "Run Query" }).click();

  // Wait for query results to appear - ChartCard renders a <canvas> via
  // svelte-chartjs, there is no ".chart-container" class in the app.
  await expect(page.locator("canvas")).toBeVisible({
    timeout: 10000,
  });
});
