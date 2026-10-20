import { describe, it, expect, afterEach, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { Layout } from "./Layout";

const endpoints = vi.hoisted(() => ({
  value: {
    authorization_endpoint: "https://idp.test/authorize",
    token_endpoint: "https://idp.test/token",
  } as { end_session_endpoint?: string } & Record<string, string>,
}));
const mode = vi.hoisted(() => ({ demo: true }));

vi.mock("../lib/oidc", () => ({
  getOidcEndpoints: vi.fn(async () => endpoints.value),
}));

vi.mock("../lib/api", () => ({
  OIDC_ISSUER: "https://idp.test",
  OIDC_CLIENT_ID: "client-123",
  get DEMO_MODE() {
    return mode.demo;
  },
  me: vi.fn().mockResolvedValue({ kind: "anonymous" }),
  checkHealth: vi.fn().mockResolvedValue({ version: "0" }),
  demoInfo: vi.fn().mockResolvedValue({ schools: [] }),
  demoReset: vi.fn().mockResolvedValue(undefined),
}));

afterEach(cleanup);

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

describe("Layout logout", () => {
  function renderLoggedIn() {
    localStorage.clear();
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
  }

  async function signOut() {
    vi.mocked((await import("../lib/api")).me).mockResolvedValue({
      kind: "authenticated",
      id: 1,
      username: "u",
      is_admin: false,
      schools: [],
    });
    renderLoggedIn();
    await userEvent.click(
      await screen.findByRole("button", { name: "Sign out" }),
    );
  }

  it("goes to end_session_endpoint with client_id and post_logout_redirect_uri", async () => {
    mode.demo = false;
    endpoints.value = {
      authorization_endpoint: "a",
      token_endpoint: "t",
      end_session_endpoint: "https://idp.test/logout",
    };
    const assigned = { href: "" };
    vi.stubGlobal("location", {
      ...window.location,
      origin: "https://glow.test",
      set href(v: string) {
        assigned.href = v;
      },
    });
    await signOut();
    await waitFor(() => expect(assigned.href).not.toBe(""));
    const url = new URL(assigned.href);
    expect(url.origin + url.pathname).toBe("https://idp.test/logout");
    expect(url.searchParams.get("client_id")).toBe("client-123");
    expect(url.searchParams.get("post_logout_redirect_uri")).toBe(
      "https://glow.test/en/login",
    );
    vi.unstubAllGlobals();
  });

  it("clears auth and goes to /login without an end_session_endpoint", async () => {
    mode.demo = false;
    endpoints.value = { authorization_endpoint: "a", token_endpoint: "t" };
    await signOut();
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeTruthy();
  });
});
