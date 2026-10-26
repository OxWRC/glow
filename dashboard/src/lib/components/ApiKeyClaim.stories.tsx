import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import { ApiKeyClaim } from "./ApiKeyClaim";

const success = getExample("api-keys.claim.success")!;
const gone = getExample("api-keys.claim.gone")!;

const meta = {
  title: "Components/ApiKeyClaim",
  component: ApiKeyClaim,
  args: { locale: "en", claimToken: success.request.token },
} satisfies Meta<typeof ApiKeyClaim>;
export default meta;
type Story = StoryObj<typeof meta>;

const claimSpy = fn();

export const RevealsKeyOnClick: Story = {
  beforeEach: () => {
    claimSpy.mockClear();
  },
  parameters: {
    msw: {
      handlers: [
        http.post("/api/api-keys/claim", async ({ request }) => {
          claimSpy(await request.json());
          return HttpResponse.json(success.response, {
            status: success.status,
          });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    // Loading the page must not spend the one-time token.
    await expect(claimSpy).not.toHaveBeenCalled();
    await expect(canvas.queryByText(success.response.key)).toBeNull();
    await userEvent.click(
      canvas.getByRole("button", { name: "Reveal my API key" }),
    );
    await waitFor(() => expect(claimSpy).toHaveBeenCalledWith(success.request));
    await expect(await canvas.findByText(success.response.key)).toBeVisible();
    await expect(canvas.getByRole("alert")).toHaveTextContent(
      /only time the key is shown/,
    );
    await expect(
      canvas.getByText(/curl -X DELETE -H "X-API-Key: glow_/),
    ).toHaveTextContent(/\/api\/api-keys\/self$/);
    await expect(
      canvas.queryByRole("button", { name: "Reveal my API key" }),
    ).toBeNull();
  },
};

export const LinkAlreadyUsed: Story = {
  args: { claimToken: gone.request.token },
  parameters: {
    msw: {
      handlers: [
        http.post("/api/api-keys/claim", () =>
          HttpResponse.json(gone.response, { status: gone.status }),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole("button", { name: "Reveal my API key" }),
    );
    await expect(await canvas.findByRole("alert")).toHaveTextContent(
      /invalid, has expired or has already been used/,
    );
    await expect(
      canvas.queryByRole("button", { name: "Reveal my API key" }),
    ).toBeNull();
  },
};

export const MissingToken: Story = {
  args: { claimToken: null },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("alert")).toHaveTextContent(
      /Ask your administrator for a new link/,
    );
    await expect(canvas.queryByRole("button")).toBeNull();
  },
};

export const CopyFails: Story = {
  beforeEach: () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("denied")) },
    });
    return () => {
      delete (navigator as { clipboard?: unknown }).clipboard;
    };
  },
  parameters: {
    msw: {
      handlers: [
        http.post("/api/api-keys/claim", () =>
          HttpResponse.json(success.response),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole("button", { name: "Reveal my API key" }),
    );
    await userEvent.click(await canvas.findByRole("button", { name: "Copy" }));
    await expect(await canvas.findByText(/Couldn.t copy/)).toBeVisible();
    await expect(canvas.getByText(success.response.key)).toBeVisible();
  },
};
