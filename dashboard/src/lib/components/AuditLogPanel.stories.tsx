import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import { AuditLogPanel } from "./AuditLogPanel";

const listExample = getExample("admin.audit.list")!;

const meta = {
  title: "Components/AuditLogPanel",
  component: AuditLogPanel,
  args: { token: "t", locale: "en" },
} satisfies Meta<typeof AuditLogPanel>;
export default meta;
type Story = StoryObj<typeof meta>;

const listOk = http.get("/api/admin/audit", () =>
  HttpResponse.json(listExample.response),
);

export const WhoWhatWhen: Story = {
  parameters: { msw: { handlers: [listOk] } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const keyRow = await canvas.findByRole("row", {
      name: /analyst@example\.org#4f9c2ab/,
    });
    await expect(within(keyRow).getByText("data.export")).toBeVisible();
    await expect(within(keyRow).getByText("200")).toBeVisible();
    await expect(
      canvas.getByRole("row", { name: /system:scheduler/ }),
    ).toBeVisible();
    await expect(canvas.getByRole("row", { name: /anonymous/ })).toBeVisible();

    await expect(within(keyRow).getByText(/203\.0\.113\.7/)).not.toBeVisible();
    await userEvent.click(within(keyRow).getByText("Show"));
    await expect(within(keyRow).getByText(/203\.0\.113\.7/)).toBeVisible();
  },
};

let lastQuery = new URLSearchParams();

export const FiltersSentToApi: Story = {
  beforeEach: () => {
    lastQuery = new URLSearchParams();
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/audit", ({ request }) => {
          lastQuery = new URL(request.url).searchParams;
          return HttpResponse.json(
            lastQuery.get("who")
              ? [listExample.response[0]]
              : listExample.response,
          );
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /system:scheduler/ });
    const form = canvas.getByRole("search", { name: "Filter audit log" });

    await userEvent.type(within(form).getByLabelText("Who"), "4f9c2ab");
    await userEvent.type(within(form).getByLabelText("What"), "export");
    await userEvent.type(
      within(form).getByLabelText("From"),
      "2026-10-01T00:00",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Filter" }));

    await waitFor(() => expect(lastQuery.get("who")).toBe("4f9c2ab"));
    await expect(lastQuery.get("what")).toBe("export");
    await expect(lastQuery.get("since")).toBe(
      new Date("2026-10-01T00:00").toISOString(),
    );
    await expect(lastQuery.has("until")).toBe(false);
    await waitFor(() =>
      expect(
        canvas.queryByRole("row", { name: /system:scheduler/ }),
      ).toBeNull(),
    );

    await userEvent.click(within(form).getByRole("button", { name: "Clear" }));
    await canvas.findByRole("row", { name: /system:scheduler/ });
    await expect(lastQuery.has("who")).toBe(false);
  },
};

const page = (offset: number, n: number) =>
  Array.from({ length: n }, (_, i) => ({
    ...listExample.response[2],
    id: 1000 - offset - i,
    actor: `system:poll-${offset + i}`,
  }));

export const LoadMore: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/audit", ({ request }) => {
          const before = new URL(request.url).searchParams.get("before_id");
          // The first page's last id is 901 (1000 - 99).
          if (before && before !== "901") return HttpResponse.json([]);
          return HttpResponse.json(before ? page(100, 1) : page(0, 100));
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /system:poll-99/ });
    await userEvent.click(canvas.getByRole("button", { name: "Load more" }));
    await canvas.findByRole("row", { name: /system:poll-100/ });
    await expect(canvas.getAllByRole("row")).toHaveLength(102);
    await expect(
      canvas.queryByRole("button", { name: "Load more" }),
    ).toBeNull();
  },
};

let exportQuery: URLSearchParams | null = null;
const saved = fn<(blob: Blob) => string>(() => "blob:audit");
const realCreateObjectURL = URL.createObjectURL;

export const ExportDownloadsAppliedFilters: Story = {
  beforeEach: () => {
    exportQuery = null;
    saved.mockClear();
    URL.createObjectURL = saved;
    return () => {
      URL.createObjectURL = realCreateObjectURL;
    };
  },
  parameters: {
    msw: {
      handlers: [
        listOk,
        http.get("/api/admin/audit/export", ({ request }) => {
          exportQuery = new URL(request.url).searchParams;
          return new HttpResponse(
            `${JSON.stringify(listExample.response[0])}\n`,
            {
              headers: { "Content-Type": "application/x-ndjson" },
            },
          );
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /system:scheduler/ });
    const form = canvas.getByRole("search", { name: "Filter audit log" });
    await userEvent.type(within(form).getByLabelText("Who"), "analyst");
    await userEvent.click(within(form).getByRole("button", { name: "Filter" }));
    // Typed but not applied: must not leak into the export.
    await userEvent.type(within(form).getByLabelText("What"), "unapplied");

    await userEvent.click(
      within(form).getByRole("button", { name: "Download JSONL" }),
    );
    await waitFor(() => expect(saved).toHaveBeenCalled());
    await expect(exportQuery?.get("who")).toBe("analyst");
    await expect(exportQuery?.has("what")).toBe(false);
    await expect(await saved.mock.calls[0][0].text()).toBe(
      `${JSON.stringify(listExample.response[0])}\n`,
    );
  },
};

export const ExportFails: Story = {
  parameters: {
    msw: {
      handlers: [
        listOk,
        http.get("/api/admin/audit/export", () =>
          HttpResponse.json(
            { detail: "Admin access required" },
            { status: 403 },
          ),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /system:scheduler/ });
    await userEvent.click(
      canvas.getByRole("button", { name: "Download JSONL" }),
    );
    await expect(await canvas.findByRole("alert")).toHaveTextContent(
      /Export failed/,
    );
  },
};

export const Empty: Story = {
  parameters: {
    msw: {
      handlers: [http.get("/api/admin/audit", () => HttpResponse.json([]))],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText("No matching audit events."),
    ).toBeVisible();
  },
};

export const LoadError: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/audit", () =>
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
    ).toHaveTextContent(/Failed to load audit log/);
  },
};
