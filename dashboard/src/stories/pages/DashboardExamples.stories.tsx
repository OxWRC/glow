/**
 * Dashboard stories (refactored to use contract examples).
 *
 * This demonstrates the new pattern using apiResponses configuration
 * instead of inline MSW handlers.
 */

import { useEffect } from "react";
import type { Meta, StoryFn, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse, delay } from "msw";
import { expect, within, waitFor, userEvent } from "storybook/test";
import { DashboardPage } from "../../routes/DashboardPage";
import {
  withApiResponses,
  withMalformedJson,
} from "../../lib/mocks/storyHelpers";
import { getExample } from "../../lib/mocks/contractExamples";
import { AuthProvider, useAuth } from "../../auth/AuthContext";
import type { MeResponse } from "../../lib/api";

const meta = {
  title: "Pages/Dashboard (Contract Examples)",
  component: DashboardPage,
  parameters: {
    layout: "fullscreen",
  },
  tags: ["autodocs"],
} satisfies Meta<typeof DashboardPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// Mock identities. Unlike the old Svelte stories - which primed authStore
// with the `admin.me.*` examples (UserRead shape: school_ids/school_names
// parallel arrays) - these come straight from the `me.*` contract examples,
// which are already shaped as MeResponse (kind/id/username/is_admin/schools),
// because that's the same data MSW serves back from GET /me. No field
// mapping needed.
const mockUser = getExample("me.authenticated")?.response as MeResponse;
const mockAdminUser = getExample("me.authenticated.admin")
  ?.response as MeResponse;
// The old stories' "no schools" user primed authStore with the
// `admin.me.no-schools` user while separately configuring "GET /me" to
// return `me.anonymous` (its own comment: "User with no schools appears as
// anonymous"). There's no MeAuthenticated example for a schools-less user,
// and the real /me contract already treats that case as anonymous, so we
// prime auth with that same anonymous identity rather than inventing a
// synthetic authenticated-with-no-schools one.
const anonymousIdentity = getExample("me.anonymous")?.response as MeResponse;

/**
 * Replaces the old `authStore.login(token, user)` decorator call.
 * `AuthState.user`/`authStore.login()` were dropped as dead code (Task 8) -
 * only these stories ever called `.login()`, and it didn't match how the
 * real app authenticates anyway. This wraps the story in the real
 * `AuthProvider` and primes it via `setIdentity`, same as the app does after
 * a real login. Decorators can't call hooks directly, so a small wrapper
 * component does the priming in an effect.
 */
function withAuth(identity: MeResponse, token = "mock-jwt-token") {
  return (Story: StoryFn) => {
    function PrimedAuth() {
      const { setIdentity } = useAuth();
      // Mount-only priming, like DashboardPage's own bootstrap effect - not
      // meant to re-run if `identity`/`token` identity changes later.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      useEffect(() => setIdentity(identity, token), []);
      return <Story />;
    }
    return (
      <AuthProvider>
        <PrimedAuth />
      </AuthProvider>
    );
  };
}

// Default story - successful query with no aggregations
export const Default: Story = {
  parameters: {
    msw: withApiResponses({
      "POST /auth/login": "auth.login.success",
      "GET /admin/me": "admin.me.user",
      "GET /me": "me.authenticated",
      "GET /dimensions": "dimensions.dataset",
      "GET /query": "query.period-based.simple",
    }),
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const heading = canvas.getByRole("heading", {
          level: 1,
          name: /Explore Data/i,
        });
        await expect(heading).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
        await expect(queryButton).not.toBeDisabled();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await waitFor(
      async () => {
        const chartCanvas = canvasElement.querySelector("canvas");
        await expect(chartCanvas).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const versionsNote = canvas.getByText(
      /multiple compatible versions of the same questionnaire/i,
    );
    await expect(versionsNote).toBeInTheDocument();

    const rescaledNote = canvas.getByText(
      /values have been rescaled to allow comparison between form versions/i,
    );
    await expect(rescaledNote).toBeInTheDocument();

    const hideTableButton = canvas.getByRole("button", { name: /Hide Table/i });
    await expect(hideTableButton).toBeInTheDocument();
  },
};

// Grouped trends: two variables by sex across periods draw small multiples,
// switchable between one graph per variable and one per group
export const GroupedTrends: Story = {
  parameters: {
    msw: withApiResponses({
      "GET /me": "me.authenticated",
      "GET /dimensions": "dimensions.dataset",
      "GET /query": "query.period-based.multi-variable-dimensions",
    }),
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    const queryButton = await canvas.findByRole(
      "button",
      { name: /Run Query/i },
      { timeout: 3000 },
    );
    await waitFor(() => expect(queryButton).not.toBeDisabled());
    await userEvent.click(queryButton);

    // Default: one graph per variable
    const perVariable = await canvas.findByRole(
      "radio",
      { name: "Variable" },
      { timeout: 3000 },
    );
    await expect(perVariable).toBeChecked();
    const captions = () =>
      canvas.getAllByRole("figure").map((f) => f.textContent);
    await expect(captions()).toEqual([
      "BeWell questionnaire: Psychological wellbeing: I've been feeling optimistic about the future",
      "BeWell questionnaire: Psychological wellbeing: I've been feeling useful",
    ]);

    // Switch to one graph per group
    await userEvent.click(canvas.getByRole("radio", { name: "Group" }));
    await waitFor(() => expect(captions()).toEqual(["Sex: M", "Sex: F"]));
  },
};

// Admin user can see all schools
export const AdminUser: Story = {
  parameters: {
    msw: withApiResponses({
      "GET /admin/me": "admin.me.admin",
      "GET /me": "me.authenticated.admin",
      "GET /dimensions": "dimensions.dataset",
      "GET /query": "query.period-based.simple",
    }),
  },
  decorators: [withAuth(mockAdminUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const heading = canvas.getByRole("heading", { level: 1 });
        await expect(heading).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    // Demo mode is off here, so no demo banner.
    await expect(
      canvas.queryByRole("complementary", { name: "Demo deployment" }),
    ).toBeNull();

    await waitFor(
      async () => {
        const schoolSelect = canvas.getByRole("combobox", { name: /School/i });
        await expect(schoolSelect).toBeInTheDocument();

        const options = canvas.getAllByRole("option");
        await expect(options.length).toBeGreaterThan(1);
      },
      { timeout: 3000 },
    );
  },
};

// Suppressed focus school
export const SuppressedFocusSchool: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/query", async () => {
          await delay(200);
          return HttpResponse.json({
            query: {
              school_id: 1,
              variables: ["bewell_questionnaire__bw_wbeing_1"],
              dimensions: [],
              variable_prefixes: [],
            },
            dimensions: [],
            periods: ["2023-2024"],
            variables: [
              {
                variable: "bewell_questionnaire__bw_wbeing_1",
                periods: {
                  "2023-2024": {
                    suppressed: true,
                    suppression_reason: "small-n",
                    cells: null,
                  },
                },
              },
            ],
          });
        }),
        ...withApiResponses({
          "GET /admin/me": "admin.me.user",
          "GET /me": "me.authenticated",
          "GET /dimensions": "dimensions.dataset",
        }).handlers,
      ],
    },
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).not.toBeDisabled();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await waitFor(
      async () => {
        const suppressionMessage = canvas.getByText(/All data is suppressed/i);
        await expect(suppressionMessage).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const chartCanvas = canvasElement.querySelector("canvas");
    await expect(chartCanvas).not.toBeInTheDocument();
  },
};

// No schools available
export const NoSchools: Story = {
  parameters: {
    msw: withApiResponses({
      "GET /admin/me": "admin.me.no-schools",
      "GET /me": "me.anonymous", // User with no schools appears as anonymous
      "GET /dimensions": "dimensions.dataset",
    }),
  },
  decorators: [withAuth(anonymousIdentity)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const heading = canvas.getByRole("heading", { level: 1 });
        await expect(heading).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
        await expect(queryButton).not.toBeDisabled();
      },
      { timeout: 3000 },
    );
  },
};

// Loading state (query never resolves)
export const Loading: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/query", async () => {
          await new Promise(() => {});
          return HttpResponse.json({});
        }),
        ...withApiResponses({
          "GET /admin/me": "admin.me.user",
          "GET /me": "me.authenticated",
          "GET /dimensions": "dimensions.dataset",
        }).handlers,
      ],
    },
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await new Promise((resolve) => setTimeout(resolve, 200));

    await waitFor(
      async () => {
        const queryingButton = canvas.getByRole("button", {
          name: /Querying/i,
        });
        await expect(queryingButton).toBeInTheDocument();
        await expect(queryingButton).toBeDisabled();
      },
      { timeout: 3000 },
    );
  },
};

// Error - Unauthorized (403)
export const ErrorUnauthorized: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/query", async () => {
          await delay(200);
          return HttpResponse.json(
            { detail: "You do not have permission to access this resource." },
            { status: 403 },
          );
        }),
        ...withApiResponses({
          "GET /admin/me": "admin.me.user",
          "GET /me": "me.authenticated",
          "GET /dimensions": "dimensions.dataset",
        }).handlers,
      ],
    },
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await waitFor(
      async () => {
        const errorMessage = canvas.getByText(/You do not have permission/i);
        await expect(errorMessage).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  },
};

// Error - Bad Request (400)
export const ErrorBadRequest: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/query", async () => {
          await delay(200);
          return HttpResponse.json(
            { detail: "Variable bewell_questionnaire__unknown not found" },
            { status: 400 },
          );
        }),
        ...withApiResponses({
          "GET /admin/me": "admin.me.user",
          "GET /me": "me.authenticated",
          "GET /dimensions": "dimensions.dataset",
        }).handlers,
      ],
    },
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await waitFor(
      async () => {
        const errorMessage = canvas.getByText(/Variable.*not found/i);
        await expect(errorMessage).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  },
};

// Error - Server Error (500)
export const ErrorServerError: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/query", async () => {
          await delay(200);
          return HttpResponse.json(
            {
              detail:
                "The server encountered an error processing your request.",
            },
            { status: 500 },
          );
        }),
        ...withApiResponses({
          "GET /admin/me": "admin.me.user",
          "GET /me": "me.authenticated",
          "GET /dimensions": "dimensions.dataset",
        }).handlers,
      ],
    },
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await waitFor(
      async () => {
        const errorMessage = canvas.getByText(/server encountered an error/i);
        await expect(errorMessage).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  },
};

// Error - Malformed JSON
export const ErrorMalformedJSON: Story = {
  parameters: {
    msw: withMalformedJson(),
  },
  decorators: [withAuth(mockUser)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);

    await waitFor(
      async () => {
        const queryButton = canvas.getByRole("button", { name: /Run Query/i });
        await expect(queryButton).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    const queryButton = canvas.getByRole("button", { name: /Run Query/i });
    await userEvent.click(queryButton);

    await waitFor(
      async () => {
        const errorMessage = canvas.getByText(/server encountered an error/i);
        await expect(errorMessage).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  },
};
