import type { Meta, StoryFn, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { MemoryRouter, useLocation } from "react-router-dom";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { AuthProvider } from "../../auth/AuthContext";
import { getExample } from "../mocks/contractExamples";
import { createHandlersFromExamples } from "../mocks/handlersFromExamples";
import { DemoBanner } from "./DemoBanner";

const infoExample = getExample("demo.info")!;
const schools = infoExample.response.schools as { name: string }[];

// Test-only probe so stories can assert where the banner navigated.
function LocationProbe() {
  return <p>location: {useLocation().pathname}</p>;
}

const meta = {
  title: "Components/DemoBanner",
  component: DemoBanner,
  args: { locale: "en" },
  decorators: [
    (Story: StoryFn) => (
      <MemoryRouter initialEntries={["/en"]}>
        <AuthProvider>
          <Story />
          <LocationProbe />
        </AuthProvider>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof DemoBanner>;

export default meta;
type Story = StoryObj<typeof meta>;

const infoOk = http.get("/api/demo/info", () =>
  HttpResponse.json(infoExample.response),
);

export const Collapsed: Story = {
  parameters: { msw: { handlers: [infoOk] } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const summary = canvas.getByText(/click to see login details/i);
    await expect(summary).toBeVisible();
    const details = summary.closest("details")!;
    await expect(details).not.toHaveAttribute("open");
    // Wait for the data so "not visible" isn't just the loading state.
    const table = await canvas.findByRole("table", { hidden: true });
    await expect(table).not.toBeVisible();
    await userEvent.click(summary);
    await expect(details).toHaveAttribute("open");
    await expect(await canvas.findByRole("table")).toBeVisible();
  },
};

export const Expanded: Story = {
  parameters: { msw: { handlers: [infoOk] } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(/click to see login details/i));
    for (const school of schools) {
      await expect(
        await canvas.findByRole("cell", { name: school.name }),
      ).toBeVisible();
    }
    await expect(canvas.getByText(/choose Admin/i)).toBeVisible();
  },
};

const resetSpy = fn();
// seed_demo re-inserts schools, so their IDs change on every reset.
const afterReset = { schools: [{ id: 41, name: "Focus School Academy" }] };
let resetDone = false;

export const ResetConfirm: Story = {
  beforeEach: () => {
    resetDone = false;
    resetSpy.mockClear();
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/demo/info", () =>
          HttpResponse.json(resetDone ? afterReset : infoExample.response),
        ),
        http.post("/api/demo/reset", () => {
          resetSpy();
          resetDone = true;
          return new HttpResponse(null, { status: 204 });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(/click to see login details/i));
    await expect(await canvas.findByRole("cell", { name: "1" })).toBeVisible();

    await userEvent.click(canvas.getByRole("button", { name: "Reset demo" }));
    const dialog = await canvas.findByRole("dialog");
    await expect(dialog).toBeVisible();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    await waitFor(() => expect(canvas.queryByRole("dialog")).toBeNull());
    await expect(resetSpy).not.toHaveBeenCalled();

    await userEvent.click(canvas.getByRole("button", { name: "Reset demo" }));
    await userEvent.click(
      within(await canvas.findByRole("dialog")).getByRole("button", {
        name: "Reset everything",
      }),
    );
    await waitFor(() => expect(resetSpy).toHaveBeenCalledTimes(1));
    await expect(await canvas.findByRole("status")).toHaveTextContent(
      "Demo reset",
    );
    await expect(canvas.getByText("location: /en/login")).toBeVisible();
    // The school list is refetched: stale IDs would fail a school login.
    await expect(await canvas.findByRole("cell", { name: "41" })).toBeVisible();
    await expect(canvas.queryByRole("cell", { name: "1" })).toBeNull();
  },
};

const storedAuth = JSON.stringify({
  token: "demo-token",
  identity: { kind: "authenticated" },
});

export const ResetFailed: Story = {
  beforeEach: () => {
    localStorage.setItem("auth", storedAuth);
    return () => localStorage.removeItem("auth");
  },
  parameters: {
    msw: {
      handlers: createHandlersFromExamples({
        "POST /demo/reset": "demo.reset.unavailable",
      }),
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(/click to see login details/i));
    await userEvent.click(canvas.getByRole("button", { name: "Reset demo" }));
    await userEvent.click(
      within(await canvas.findByRole("dialog")).getByRole("button", {
        name: "Reset everything",
      }),
    );
    await waitFor(() =>
      expect(canvas.getByRole("status")).toHaveTextContent(
        "Demo reset failed.",
      ),
    );
    // Still signed in and still on the same page.
    await expect(localStorage.getItem("auth")).toBe(storedAuth);
    await expect(canvas.getByText("location: /en")).toBeVisible();
  },
};

export const InfoError: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get(
          "/api/demo/info",
          () => new HttpResponse(null, { status: 500 }),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(/click to see login details/i));
    await expect(
      await canvas.findByText("Could not load the school list."),
    ).toBeVisible();
    await expect(canvas.queryByRole("table")).toBeNull();
    await expect(
      canvas.getByRole("button", { name: "Reset demo" }),
    ).toBeVisible();
  },
};

export const NoSchools: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/demo/info", () => HttpResponse.json({ schools: [] })),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(/click to see login details/i));
    await expect(
      await canvas.findByText("No schools are loaded."),
    ).toBeVisible();
    await expect(canvas.queryByRole("table")).toBeNull();
  },
};
