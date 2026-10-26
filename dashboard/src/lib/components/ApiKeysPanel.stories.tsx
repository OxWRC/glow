import type { Meta, StoryObj } from "@storybook/react-vite";
import { http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import { getExample } from "../mocks/contractExamples";
import { ApiKeysPanel } from "./ApiKeysPanel";

const listExample = getExample("admin.api-keys.list")!;
const createdExample = getExample("admin.api-keys.created")!;

const meta = {
  title: "Components/ApiKeysPanel",
  component: ApiKeysPanel,
  args: { token: "t", locale: "en" },
} satisfies Meta<typeof ApiKeysPanel>;
export default meta;
type Story = StoryObj<typeof meta>;

const listOk = http.get("/api/admin/api-keys", () =>
  HttpResponse.json(listExample.response),
);

// window.confirm() is the guard the user asked for; stub it per story.
function stubConfirm(answer: boolean) {
  const spy = fn(() => answer);
  const original = window.confirm;
  window.confirm = spy;
  return { spy, restore: () => (window.confirm = original) };
}

export const KeyStatuses: Story = {
  parameters: { msw: { handlers: [listOk] } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const unclaimed = await canvas.findByRole("row", {
      name: /newstarter@example\.org/,
    });
    await expect(within(unclaimed).getByText("Unclaimed")).toBeVisible();
    await expect(
      within(unclaimed).getByRole("button", {
        name: "Revoke newstarter@example.org",
      }),
    ).toBeVisible();
    const active = canvas.getByRole("row", { name: /WRC analysis 2026/ });
    await expect(within(active).getByText("Active")).toBeVisible();
    await expect(
      within(active).getByText("Annual report pipeline"),
    ).toBeVisible();
    await expect(within(active).getByText("4")).toBeVisible();
    const expired = canvas.getByRole("row", { name: /Old pipeline/ });
    await expect(within(expired).getByText("Expired")).toBeVisible();
    await expect(
      within(expired).queryByRole("button", { name: /Revoke/ }),
    ).toBeNull();
    const revoked = canvas.getByRole("row", { name: /Leaked test key/ });
    await expect(within(revoked).getByText("Revoked")).toBeVisible();
    await expect(
      within(revoked).getByRole("button", {
        name: "Regenerate Leaked test key",
      }),
    ).toBeVisible();
    await expect(canvas.queryByText(/glow_/)).toBeNull();
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

export const CreateEmailsClaimLink: Story = {
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
              ? [createdExample.response, ...listExample.response]
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
    await userEvent.type(canvas.getByLabelText("Email"), "analyst@example.org");
    await userEvent.type(canvas.getByLabelText(/^Name/), "New analysis key");
    await userEvent.type(
      canvas.getByLabelText(/^Notes/),
      "Termly wellbeing report",
    );
    await userEvent.type(canvas.getByLabelText(/^Expires in/), "90");
    await userEvent.click(
      canvas.getByRole("button", { name: "Create key and email link" }),
    );
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(createdExample.request),
    );
    await expect(await canvas.findByRole("status")).toHaveTextContent(
      "Claim link sent to analyst@example.org.",
    );
    const row = await canvas.findByRole("row", { name: /New analysis key/ });
    await expect(within(row).getByText("Unclaimed")).toBeVisible();
    await expect(canvas.queryByRole("dialog")).toBeNull();
  },
};

export const CreateWithoutOptionalFields: Story = {
  beforeEach: () => {
    createSpy.mockClear();
  },
  parameters: {
    msw: {
      handlers: [
        listOk,
        http.post("/api/admin/api-keys", async ({ request }) => {
          createSpy(await request.json());
          return HttpResponse.json(
            {
              ...createdExample.response,
              email: "x@example.org",
              name: null,
              notes: null,
              expires_at: null,
            },
            { status: 201 },
          );
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /WRC analysis 2026/ });
    await userEvent.type(canvas.getByLabelText("Email"), "x@example.org");
    await userEvent.click(
      canvas.getByRole("button", { name: "Create key and email link" }),
    );
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith({
        email: "x@example.org",
        name: null,
        notes: null,
        expires_in_days: null,
      }),
    );
  },
};

let createFailedListCalls = 0;

export const EmailNotConfigured: Story = {
  beforeEach: () => {
    createFailedListCalls = 0;
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/api-keys", () => {
          createFailedListCalls += 1;
          return HttpResponse.json(listExample.response);
        }),
        http.post("/api/admin/api-keys", () =>
          HttpResponse.json(
            { detail: "Email is not configured" },
            { status: 503 },
          ),
        ),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await canvas.findByRole("row", { name: /WRC analysis 2026/ });
    const rowsBefore = canvas.getAllByRole("row").length;
    await userEvent.type(canvas.getByLabelText("Email"), "a@example.org");
    await userEvent.click(
      canvas.getByRole("button", { name: "Create key and email link" }),
    );
    await expect(await canvas.findByRole("alert")).toHaveTextContent(
      /server encountered an error/,
    );
    await expect(canvas.getByRole("status")).toHaveTextContent("");
    await expect(canvas.getAllByRole("row")).toHaveLength(rowsBefore);
    await expect(createFailedListCalls).toBe(1);
  },
};

const revokeSpy = fn();
let revoked = false;
let confirmStub: ReturnType<typeof stubConfirm>;

export const RevokeConfirm: Story = {
  beforeEach: () => {
    revoked = false;
    revokeSpy.mockClear();
    confirmStub = stubConfirm(false);
    return confirmStub.restore;
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/api-keys", () =>
          HttpResponse.json(
            listExample.response.map((k: { id: number }) =>
              revoked && k.id === 3
                ? {
                    ...k,
                    status: "revoked",
                    revoked_at: "2026-10-07T11:00:00Z",
                  }
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
    const revoke = within(row).getByRole("button", {
      name: "Revoke WRC analysis 2026",
    });
    await userEvent.click(revoke);
    await expect(confirmStub.spy).toHaveBeenCalledWith(
      "Revoke “WRC analysis 2026”? Anything using it will stop working immediately.",
    );
    await expect(revokeSpy).not.toHaveBeenCalled();

    confirmStub.spy.mockReturnValue(true);
    await userEvent.click(revoke);
    await waitFor(() => expect(revokeSpy).toHaveBeenCalledWith("3"));
    const updated = await canvas.findByRole("row", {
      name: /WRC analysis 2026/,
    });
    await waitFor(() =>
      expect(within(updated).getByText("Revoked")).toBeVisible(),
    );
  },
};

const regenerateSpy = fn();
let regenerated = false;

export const RegenerateConfirm: Story = {
  beforeEach: () => {
    regenerated = false;
    regenerateSpy.mockClear();
    confirmStub = stubConfirm(true);
    return confirmStub.restore;
  },
  parameters: {
    msw: {
      handlers: [
        http.get("/api/admin/api-keys", () =>
          HttpResponse.json(
            regenerated
              ? [
                  {
                    ...listExample.response[3],
                    id: 6,
                    status: "unclaimed",
                    revoked_at: null,
                    claimed_at: null,
                  },
                  ...listExample.response,
                ]
              : listExample.response,
          ),
        ),
        http.post("/api/admin/api-keys/:id/regenerate", ({ params }) => {
          regenerateSpy(params.id);
          regenerated = true;
          return HttpResponse.json(
            {
              ...listExample.response[3],
              id: 6,
              status: "unclaimed",
              revoked_at: null,
              claimed_at: null,
            },
            { status: 201 },
          );
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const row = await canvas.findByRole("row", { name: /Leaked test key/ });
    await userEvent.click(
      within(row).getByRole("button", { name: "Regenerate Leaked test key" }),
    );
    await expect(confirmStub.spy).toHaveBeenCalledWith(
      expect.stringContaining("intern@example.org"),
    );
    await waitFor(() => expect(regenerateSpy).toHaveBeenCalledWith("1"));
    await expect(await canvas.findByRole("status")).toHaveTextContent(
      "Claim link sent to intern@example.org.",
    );
    await waitFor(() =>
      expect(
        canvas.getAllByRole("row", { name: /Leaked test key/ }),
      ).toHaveLength(2),
    );
  },
};
