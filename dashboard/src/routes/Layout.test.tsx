import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { Layout } from "./Layout";

vi.mock("../lib/api", () => ({
  COGNITO_DOMAIN: "",
  COGNITO_CLIENT_ID: "",
  DEMO_MODE: true,
  me: vi.fn().mockResolvedValue({ kind: "anonymous" }),
  checkHealth: vi.fn().mockResolvedValue({ version: "0" }),
  demoInfo: vi.fn().mockResolvedValue({ schools: [] }),
  demoReset: vi.fn().mockResolvedValue(undefined),
}));

describe("Layout demo banner", () => {
  it("keeps the reset confirmation after navigating to /login", async () => {
    HTMLDialogElement.prototype.showModal ??= function () {};
    HTMLDialogElement.prototype.close ??= function () {};
    const router = createMemoryRouter(
      [
        {
          path: "/:locale",
          element: <Layout />,
          children: [
            { index: true, element: <h1>Home</h1> },
            { path: "login", element: <h1>Sign in</h1> },
          ],
        },
      ],
      { initialEntries: ["/en"] },
    );
    render(
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>,
    );

    await userEvent.click(screen.getByText(/click to see login details/i));
    await userEvent.click(screen.getByRole("button", { name: "Reset demo" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Reset everything", hidden: true }),
    );

    await screen.findByRole("heading", { name: "Sign in" });
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe("Demo reset"),
    );
  });
});
