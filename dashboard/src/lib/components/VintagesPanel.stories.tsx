import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import { VintagesPanel } from "./VintagesPanel";

const listExample = getExample("admin.vintages.list")!;
const createdExample = getExample("admin.vintages.created")!;

const meta = {
  title: "Components/VintagesPanel",
  component: VintagesPanel,
  args: { token: "t", locale: "en" },
} satisfies Meta<typeof VintagesPanel>;
export default meta;
type Story = StoryObj<typeof meta>;

const listOk = http.get("/api/admin/vintages", () =>
  HttpResponse.json(listExample.response),
);

export const ListedNewestFirst: Story = {
  parameters: { msw: { handlers: [listOk] } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const rows = await canvas.findAllByRole("row");
    // Header row + 2 data rows, newest (adminuser) first.
    await expect(rows).toHaveLength(3);
    await expect(within(rows[1]).getByText("adminuser")).toBeVisible();
    await expect(within(rows[2]).getByText("Scheduler")).toBeVisible();
  },
};

export const Empty: Story = {
  parameters: {
    msw: {
      handlers: [http.get("/api/admin/vintages", () => HttpResponse.json([]))],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText("No vintages yet."),
    ).toBeVisible();
  },
};

export const LoadError: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/vintages", () =>
          HttpResponse.json(
            { detail: "Admin access required" },
            { status: 403 },
          ),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByRole("alert"),
    ).toHaveTextContent(/Failed to load vintages/);
  },
};

const cutSpy = fn();
let cut = false;

export const CutNowConfirmFlow: Story = {
  beforeEach: () => {
    cut = false;
    cutSpy.mockClear();
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/vintages", () =>
          HttpResponse.json(
            cut ? [...listExample.response, createdExample.response] : listExample.response,
          ),
        ),
        http.post("/api/admin/vintages", () => {
          cutSpy();
          cut = true;
          return HttpResponse.json(createdExample.response, { status: 201 });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findAllByRole("row");

    await userEvent.click(canvas.getByRole("button", { name: "Cut vintage now" }));
    let dialog = await canvas.findByRole("dialog", {
      name: "Cut a new vintage?",
    });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(canvas.queryByRole("dialog")).toBeNull());
    await expect(cutSpy).not.toHaveBeenCalled();

    await userEvent.click(canvas.getByRole("button", { name: "Cut vintage now" }));
    dialog = await canvas.findByRole("dialog", { name: "Cut a new vintage?" });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cut vintage now" }),
    );
    await waitFor(() => expect(cutSpy).toHaveBeenCalled());
    await waitFor(() => expect(canvas.queryByRole("dialog")).toBeNull());
    await expect(
      await canvas.findByRole("row", { name: /5112/ }),
    ).toBeVisible();
    await expect(canvas.getAllByRole("row")).toHaveLength(4);
  },
};

export const CutFails: Story = {
  parameters: {
    msw: {
      handlers: [
        listOk,
        http.post(
          "/api/admin/vintages",
          () => new HttpResponse("Internal Server Error", { status: 500 }),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findAllByRole("row");
    await userEvent.click(canvas.getByRole("button", { name: "Cut vintage now" }));
    const dialog = await canvas.findByRole("dialog", {
      name: "Cut a new vintage?",
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cut vintage now" }),
    );
    await expect(
      await within(dialog).findByRole("alert"),
    ).toHaveTextContent(/Failed to cut vintage/);
    await expect(within(dialog).queryByRole("alert")).not.toBeNull();
  },
};
