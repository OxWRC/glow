import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import { ApiKeysPanel } from "./ApiKeysPanel";

const listExample = getExample("admin.api-keys.list")!;
const createdExample = getExample("admin.api-keys.created")!;
const NOW = new Date("2026-10-07T12:00:00Z");

const meta = {
  title: "Components/ApiKeysPanel",
  component: ApiKeysPanel,
  args: { token: "t", locale: "en", now: NOW },
} satisfies Meta<typeof ApiKeysPanel>;
export default meta;
type Story = StoryObj<typeof meta>;

const listOk = http.get("/api/admin/api-keys", () =>
  HttpResponse.json(listExample.response),
);

export const KeyStatuses: Story = {
  parameters: { msw: { handlers: [listOk] } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const active = await canvas.findByRole("row", {
      name: /WRC analysis 2026/,
    });
    await expect(within(active).getByText("Active")).toBeVisible();
    await expect(within(active).getByText("4")).toBeVisible();
    await expect(
      within(active).getByRole("button", { name: /Revoke/ }),
    ).toBeVisible();
    const expired = canvas.getByRole("row", { name: /Old pipeline/ });
    await expect(within(expired).getByText("Expired")).toBeVisible();
    await expect(within(expired).getByText("CLI")).toBeVisible();
    const revoked = canvas.getByRole("row", { name: /Leaked test key/ });
    await expect(within(revoked).getByText("Revoked")).toBeVisible();
    await expect(within(revoked).getByText("Never")).toBeVisible();
    await expect(within(revoked).queryByRole("button")).toBeNull();
  },
};

export const Empty: Story = {
  parameters: {
    msw: {
      handlers: [http.get("/api/admin/api-keys", () => HttpResponse.json([]))],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText("No API keys yet."),
    ).toBeVisible();
  },
};

export const LoadError: Story = {
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/api-keys", () =>
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
    ).toHaveTextContent(/Failed to load API keys/);
  },
};

const createSpy = fn();
let created = false;

export const CreateRevealsKeyOnce: Story = {
  beforeEach: () => {
    created = false;
    createSpy.mockClear();
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/api-keys", () =>
          HttpResponse.json(
            created
              ? [
                  {
                    ...createdExample.response,
                    key: undefined,
                    created_by: "adminuser",
                    revoked_at: null,
                    last_used_at: null,
                    use_count: 0,
                  },
                  ...listExample.response,
                ]
              : listExample.response,
          ),
        ),
        http.post("/api/admin/api-keys", async ({ request }) => {
          createSpy(await request.json());
          created = true;
          return HttpResponse.json(createdExample.response, { status: 201 });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /WRC analysis 2026/ });
    await userEvent.type(canvas.getByLabelText("Name"), "New analysis key");
    await userEvent.clear(canvas.getByLabelText("Expires in (days)"));
    await userEvent.type(canvas.getByLabelText("Expires in (days)"), "90");
    await userEvent.click(canvas.getByRole("button", { name: "Create key" }));
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith({
        name: "New analysis key",
        expires_in_days: 90,
      }),
    );
    const dialog = await canvas.findByRole("dialog", {
      name: "Copy your new key",
    });
    await expect(
      within(dialog).getByText(createdExample.response.key),
    ).toBeVisible();
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(canvas.queryByRole("dialog")).toBeNull());
    await expect(canvas.queryByText(createdExample.response.key)).toBeNull();
    await expect(
      await canvas.findByRole("row", { name: /New analysis key/ }),
    ).toBeVisible();
  },
};

const revokeSpy = fn();
let revoked = false;

export const RevokeConfirm: Story = {
  beforeEach: () => {
    revoked = false;
    revokeSpy.mockClear();
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/api-keys", () =>
          HttpResponse.json(
            listExample.response.map((k: { id: number }) =>
              revoked && k.id === 3
                ? { ...k, revoked_at: "2026-10-07T11:00:00Z" }
                : k,
            ),
          ),
        ),
        http.delete("/api/admin/api-keys/:id", ({ params }) => {
          revokeSpy(params.id);
          revoked = true;
          return new HttpResponse(null, { status: 204 });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const row = await canvas.findByRole("row", { name: /WRC analysis 2026/ });
    await userEvent.click(within(row).getByRole("button", { name: /Revoke/ }));
    let dialog = await canvas.findByRole("dialog", { name: "Revoke key?" });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    await waitFor(() => expect(canvas.queryByRole("dialog")).toBeNull());
    await expect(revokeSpy).not.toHaveBeenCalled();

    await userEvent.click(within(row).getByRole("button", { name: /Revoke/ }));
    dialog = await canvas.findByRole("dialog", { name: "Revoke key?" });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Revoke" }),
    );
    await waitFor(() => expect(revokeSpy).toHaveBeenCalledWith("3"));
    const updated = await canvas.findByRole("row", {
      name: /WRC analysis 2026/,
    });
    await waitFor(() =>
      expect(within(updated).getByText("Revoked")).toBeVisible(),
    );
  },
};
