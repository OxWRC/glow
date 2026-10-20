import { describe, it, expect, afterEach, vi } from "vitest";

vi.mock("../runtimeConfig", () => ({
  getRuntimeConfig: () => ({
    apiBase: "/api",
    oidcIssuer: "https://idp.example.test",
    oidcClientId: "client-123",
    demoMode: false,
  }),
}));
vi.mock("./oidc", () => ({
  getOidcEndpoints: vi.fn().mockResolvedValue({
    authorization_endpoint: "https://idp.example.test/authorize",
    token_endpoint: "https://idp.example.test/token",
  }),
}));

import { exchangeCodeForToken, me } from "./api";

describe("exchangeCodeForToken", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts to the discovered token_endpoint", async () => {
    const f = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id_token: "t" }),
    });
    vi.stubGlobal("fetch", f);
    await exchangeCodeForToken("c1", "https://glow.test/auth/callback", "v1");
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0][0]).toBe("https://idp.example.test/token");
    expect(f.mock.calls[0][1].method).toBe("POST");
    const body = new URLSearchParams(f.mock.calls[0][1].body);
    expect(body.get("client_id")).toBe("client-123");
    expect(body.get("code_verifier")).toBe("v1");
  });
});

describe("me", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubStatus(status: number, body: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status,
        statusText: "",
        headers: new Headers({ "content-type": "application/json" }),
        text: async () => JSON.stringify(body),
      }),
    );
  }

  it("returns no_access on 403 no_access", async () => {
    stubStatus(403, { detail: "no_access" });
    expect(await me("tok")).toEqual({ kind: "no_access" });
  });

  it("returns anonymous on 401", async () => {
    stubStatus(401, { detail: "invalid token" });
    expect(await me("tok")).toEqual({ kind: "anonymous" });
  });
});
