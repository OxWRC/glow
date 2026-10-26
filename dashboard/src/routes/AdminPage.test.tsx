import { describe, it, expect, afterEach, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AdminPage } from "./AdminPage";

const api = vi.hoisted(() => ({
  createUser: vi.fn().mockResolvedValue({}),
}));

vi.mock("../lib/api", () => ({
  listUsers: vi.fn().mockResolvedValue([]),
  listSchools: vi.fn().mockResolvedValue([]),
  createUser: api.createUser,
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ token: "t", identity: null }),
  useIsAdmin: () => true,
}));
vi.mock("../lib/components/ApiKeysPanel", () => ({
  ApiKeysPanel: () => null,
}));
vi.mock("../lib/components/VintagesPanel", () => ({
  VintagesPanel: () => null,
}));

afterEach(() => {
  cleanup();
  api.createUser.mockClear();
});

async function openCreateForm() {
  HTMLDialogElement.prototype.showModal ??= function () {};
  HTMLDialogElement.prototype.close ??= function () {};
  const router = createMemoryRouter(
    [{ path: "/:locale", element: <AdminPage /> }],
    { initialEntries: ["/en"] },
  );
  render(<RouterProvider router={router} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /new user/i }),
  );
  return screen.getByLabelText("Email", { selector: "input" });
}

describe("AdminPage create user", () => {
  it("submits the email address", async () => {
    const field = await openCreateForm();
    expect(field.getAttribute("type")).toBe("email");
    await userEvent.type(field, "teacher@school.org");
    await userEvent.click(
      screen.getByRole("button", { name: "Create", hidden: true }),
    );
    await waitFor(() =>
      expect(api.createUser).toHaveBeenCalledWith("t", {
        email: "teacher@school.org",
        school_ids: [],
        is_admin: false,
      }),
    );
  });

  it("blocks an invalid email natively", async () => {
    const field = await openCreateForm();
    await userEvent.type(field, "not-an-email");
    await userEvent.click(
      screen.getByRole("button", { name: "Create", hidden: true }),
    );
    expect(api.createUser).not.toHaveBeenCalled();
  });
});
