// Cognito matches redirect_uri exactly, so login and callback must both use
// the one locale-free /auth/callback URL registered in cognito.tf, with the
// locale carried across the redirect in sessionStorage.
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage } from "./LoginPage";
import { AuthCallbackPage } from "./AuthCallbackPage";

const { exchangeCodeForToken, me } = vi.hoisted(() => ({
  exchangeCodeForToken: vi.fn(),
  me: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  COGNITO_DOMAIN: "auth.example.test",
  COGNITO_CLIENT_ID: "client-123",
  DEV_AUTH_BYPASS: false,
  ApiError: class extends Error {},
  devLogin: vi.fn(),
  exchangeCodeForToken,
  me,
}));

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      { path: "/auth/callback", element: <AuthCallbackPage /> },
      { path: "/:locale/login", element: <LoginPage /> },
      { path: "/:locale", element: <h1>Dashboard home</h1> },
    ],
    { initialEntries: [path] },
  );
  render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
}

describe("Cognito redirect_uri", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("login sends the locale-free callback and stashes the locale", async () => {
    const assigned = { href: "" };
    vi.stubGlobal("location", {
      ...window.location,
      origin: "https://glow.test",
      set href(v: string) {
        assigned.href = v;
      },
    });
    renderAt("/en/login");

    await waitFor(() => expect(assigned.href).not.toBe(""));
    const url = new URL(assigned.href);
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://glow.test/auth/callback",
    );
    expect(sessionStorage.getItem("oauth_locale")).toBe("en");
    vi.unstubAllGlobals();
  });

  it("callback exchanges with the same URI and returns to the stashed locale", async () => {
    sessionStorage.setItem("oauth_state", "s1");
    sessionStorage.setItem("pkce_code_verifier", "v1");
    sessionStorage.setItem("oauth_locale", "en");
    exchangeCodeForToken.mockResolvedValue({ id_token: "tok" });
    me.mockResolvedValue({
      kind: "authenticated",
      id: 1,
      username: "u",
      is_admin: false,
      schools: [],
    });

    renderAt("/auth/callback?code=c1&state=s1");

    expect(
      await screen.findByRole("heading", { name: "Dashboard home" }),
    ).toBeTruthy();
    expect(exchangeCodeForToken).toHaveBeenCalledWith(
      "c1",
      `${window.location.origin}/auth/callback`,
      "v1",
    );
    expect(sessionStorage.getItem("oauth_locale")).toBeNull();
  });

  it("callback falls back to en when the stashed locale is unknown", async () => {
    sessionStorage.setItem("oauth_locale", "xx");
    renderAt("/auth/callback?code=c1&state=wrong");

    const back = await screen.findByRole("link", { name: "Back to sign in" });
    expect(back.getAttribute("href")).toBe("/en/login");
  });
});
