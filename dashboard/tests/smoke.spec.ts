import { expect, test, request, type Page } from "@playwright/test";

declare const process: {
  env: Record<string, string | undefined>;
};

// The API host:port CI/local dev actually publishes it on (compose.yml's
// "8000:8000", unchanged by compose.test.yml) - separate from
// PLAYWRIGHT_BASE_URL, which points at the dashboard.
const apiBase = process.env.PLAYWRIGHT_API_BASE ?? "http://127.0.0.1:8000";
const scopedSchool =
  process.env.PLAYWRIGHT_SCOPED_SCHOOL ?? "Beahanberg High School";

/**
 * Drives the dev-bypass role picker on /en/login (the password form it
 * replaced called a since-removed /auth/login - see Task 8). Waits for the
 * post-login redirect to the locale root, i.e. a successful authenticated
 * landing.
 */
async function loginAsRole(
  page: Page,
  role: "admin" | "wrc" | "school",
  schoolId?: string,
) {
  await page.goto("/en/login");
  if (role === "school" && schoolId) {
    await page.getByLabel(/School ID/).fill(schoolId);
  }
  const label = role === "admin" ? "Admin" : role === "wrc" ? "WRC" : "School";
  await page.getByRole("button", { name: label, exact: true }).click();
  await page.waitForURL(/\/en\/?$/);
}

/**
 * The "school" dev-login role needs a school id up front, but GET /schools
 * requires auth and the dashboard has no anonymous listing to pick one from
 * (see Task 8's report) - so resolve the seeded, ODK-connected school's id
 * directly against the API via a throwaway admin dev-login, rather than
 * relying on dev-login's own no-id-given row-order fallback.
 */
async function findSchoolId(name: string): Promise<number> {
  const api = await request.newContext({ baseURL: apiBase });
  try {
    const loginRes = await api.post("/auth/dev-login", {
      data: { role: "admin" },
    });
    const { access_token: token } = await loginRes.json();
    const schoolsRes = await api.get("/schools", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const schools: { id: number; name: string }[] = await schoolsRes.json();
    const match = schools.find((s) => s.name === name);
    if (!match) {
      throw new Error(`Seeded school "${name}" not found via /schools`);
    }
    return match.id;
  } finally {
    await api.dispose();
  }
}

test("admin can log in via dev-bypass, run a query, and use the admin screen", async ({
  page,
}) => {
  await loginAsRole(page, "admin");

  await expect(
    page.getByRole("heading", { name: "Explore Data" }),
  ).toBeVisible();

  // Admin defaults to whichever school comes first alphabetically, which may
  // have no seeded data - select the school we actually seeded explicitly.
  await page
    .getByLabel("School", { exact: true })
    .selectOption({ label: scopedSchool });

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
  // react-chartjs-2, there is no ".chart-container" class in the app.
  await expect(page.locator("canvas")).toBeVisible({
    timeout: 10000,
  });

  // Test admin screen
  await page.goto("/en/admin");
  await expect(
    page.getByRole("heading", { name: "User Management" }),
  ).toBeVisible();
});

test("school-scoped dev-bypass role can log in and run a query", async ({
  page,
}) => {
  const schoolId = await findSchoolId(scopedSchool);
  await loginAsRole(page, "school", String(schoolId));

  await expect(
    page.getByRole("heading", { name: "Explore Data" }),
  ).toBeVisible();

  // Same "clear the auto-selected default variable" reasoning as above.
  for (const box of await page.getByRole("checkbox", { checked: true }).all()) {
    await box.uncheck();
  }

  await page
    .getByRole("checkbox", { name: /\[phq9_questionnaire__phq9_1\]$/ })
    .check();
  await expect(page.getByRole("button", { name: "Run Query" })).toBeEnabled();
  await page.getByRole("button", { name: "Run Query" }).click();

  await expect(page.locator("canvas")).toBeVisible({
    timeout: 10000,
  });
});
