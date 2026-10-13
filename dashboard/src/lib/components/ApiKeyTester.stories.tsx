import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import { ApiKeyTester } from "./ApiKeyTester";

const exportExample = getExample("export.default")!;
const countsExample = getExample("export.counts")!;
const unauthorizedExample = getExample("export.unauthorized")!;
const rawExample = getExample("demo.export.raw")!;
const KEY = "glow_N3wK3yAbCdEfGhIjKlMnOpQrStUvWxYz0123456789ab";

const meta = {
  title: "Components/ApiKeyTester",
  component: ApiKeyTester,
  args: { locale: "en" },
} satisfies Meta<typeof ApiKeyTester>;
export default meta;
type Story = StoryObj<typeof meta>;

const seenKeys = fn();

const okHandlers = [
  http.get("/api/export", ({ request }) => {
    seenKeys("/export", request.headers.get("X-API-Key"));
    return HttpResponse.json(exportExample.response);
  }),
  http.get("/api/export/counts", ({ request }) => {
    seenKeys("/export/counts", request.headers.get("X-API-Key"));
    return HttpResponse.json(countsExample.response);
  }),
  http.get("/api/demo/export/raw", ({ request }) => {
    seenKeys("/demo/export/raw", request.headers.get("X-API-Key"));
    return HttpResponse.json(rawExample.response);
  }),
];

export const SendsKeyAndShowsResponse: Story = {
  beforeEach: () => {
    seenKeys.mockClear();
  },
  parameters: { msw: { handlers: okHandlers } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("API key"), KEY);
    await userEvent.click(canvas.getByRole("button", { name: "Send request" }));
    const response = await canvas.findByRole("region", { name: "Response" });
    await expect(within(response).getByText(/^200/)).toBeVisible();
    await expect(
      within(response).getByLabelText("Response body"),
    ).toHaveTextContent("a1b2c3d4e5f60718");
    await expect(seenKeys).toHaveBeenCalledWith("/export", KEY);
  },
};

export const InvalidKeyShowsErrorBody: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/export", () =>
          HttpResponse.json(unauthorizedExample.response, { status: 401 }),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("API key"), "glow_nope");
    await userEvent.click(canvas.getByRole("button", { name: "Send request" }));
    const response = await canvas.findByRole("region", { name: "Response" });
    await expect(within(response).getByText(/^401/)).toBeVisible();
    await expect(
      within(response).getByLabelText("Response body"),
    ).toHaveTextContent("Invalid or expired API key");
    // A 401 is a result to inspect, not a failure of the tester itself.
    await expect(canvas.queryByRole("alert")).toBeNull();
  },
};

export const SwitchingEndpointUpdatesCurl: Story = {
  beforeEach: () => {
    seenKeys.mockClear();
  },
  parameters: { msw: { handlers: okHandlers } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(/curl .*<key>.*\/api\/export$/),
    ).toBeVisible();
    await userEvent.type(canvas.getByLabelText("API key"), KEY);
    await userEvent.click(
      canvas.getByRole("radio", { name: /export\/counts/ }),
    );
    // Key is shortened in the curl line so it isn't left on screen in full.
    await expect(
      canvas.getByText(/curl .*glow_N3wK3yA….*\/api\/export\/counts$/),
    ).toBeVisible();
    await userEvent.click(canvas.getByRole("button", { name: "Send request" }));
    const response = await canvas.findByRole("region", { name: "Response" });
    await expect(
      within(response).getByLabelText("Response body"),
    ).toHaveTextContent('"2026-T1": 120');
    await expect(seenKeys).toHaveBeenCalledWith("/export/counts", KEY);
  },
};

export const DownloadsBothDatasets: Story = {
  beforeEach: () => {
    seenKeys.mockClear();
  },
  parameters: { msw: { handlers: okHandlers } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const raw = canvas.getByRole("button", {
      name: "Download unredacted dataset",
    });
    const redacted = canvas.getByRole("button", {
      name: "Download redacted export",
    });
    await expect(raw).toBeDisabled();
    await expect(redacted).toBeDisabled();
    await userEvent.type(canvas.getByLabelText("API key"), KEY);
    await userEvent.click(raw);
    await waitFor(() =>
      expect(seenKeys).toHaveBeenCalledWith("/demo/export/raw", KEY),
    );
    await userEvent.click(redacted);
    await waitFor(() => expect(seenKeys).toHaveBeenCalledWith("/export", KEY));
    await expect(canvas.queryByRole("alert")).toBeNull();
  },
};

export const DownloadRejectedShowsAlert: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/demo/export/raw", () =>
          HttpResponse.json(unauthorizedExample.response, { status: 401 }),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("API key"), "glow_nope");
    await userEvent.click(
      canvas.getByRole("button", { name: "Download unredacted dataset" }),
    );
    await expect(await canvas.findByRole("alert")).toHaveTextContent(
      /Request failed/,
    );
  },
};

export const LargeResponseIsTruncated: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/export", () =>
          HttpResponse.json({
            ...exportExample.response,
            rows: Array.from(
              { length: 500 },
              () => exportExample.response.rows[0],
            ),
          }),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("API key"), KEY);
    await userEvent.click(canvas.getByRole("button", { name: "Send request" }));
    await expect(
      await canvas.findByText(/Showing the first 20,000 of/),
    ).toBeVisible();
    const body = canvas.getByLabelText("Response body");
    await expect(body.textContent).toHaveLength(20_000);
  },
};
