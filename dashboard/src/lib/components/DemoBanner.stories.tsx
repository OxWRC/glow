import type { Meta, StoryFn, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { AuthProvider } from "../../auth/AuthContext";
import { getExample } from "../mocks/contractExamples";
import { DemoBanner } from "./DemoBanner";

const infoExample = getExample("demo.info")!;
const schools = infoExample.response.schools as { name: string }[];

const meta = {
  title: "Components/DemoBanner",
  component: DemoBanner,
  args: { locale: "en" },
  decorators: [
    (Story: StoryFn) => (
      <MemoryRouter>
        <AuthProvider>
          <Story />
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
    await expect(canvas.queryByRole("table")).toBeNull();
    await userEvent.click(summary);
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

export const ResetConfirm: Story = {
  parameters: {
    msw: {
      handlers: [
        infoOk,
        http.post("/api/demo/reset", () => {
          resetSpy();
          return new HttpResponse(null, { status: 204 });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    resetSpy.mockClear();
    await userEvent.click(canvas.getByText(/click to see login details/i));

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
