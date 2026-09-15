import { describe, it, expect, afterEach, vi } from "vitest";
import { getRuntimeConfig, loadRuntimeConfig } from "./runtimeConfig";

(globalThis as { window?: object }).window ??= {};

describe("getRuntimeConfig", () => {
  afterEach(() => {
    delete window.__ENV__;
  });

  it("reads values from window.__ENV__ when present", () => {
    window.__ENV__ = {
      PUBLIC_API_BASE: "https://api.example.com",
      PUBLIC_COGNITO_DOMAIN: "example.auth.us-east-1.amazoncognito.com",
      PUBLIC_COGNITO_CLIENT_ID: "client123",
      PUBLIC_DEV_AUTH_BYPASS: "false",
    };
    expect(getRuntimeConfig()).toEqual({
      apiBase: "https://api.example.com",
      cognitoDomain: "example.auth.us-east-1.amazoncognito.com",
      cognitoClientId: "client123",
      devAuthBypass: false,
    });
  });

  it("defaults apiBase to /api and devAuthBypass to false when window.__ENV__ is absent", () => {
    const config = getRuntimeConfig();
    expect(config.apiBase).toBe("/api");
    expect(config.devAuthBypass).toBe(false);
  });
});

describe("loadRuntimeConfig", () => {
  afterEach(() => {
    delete window.__ENV__;
    vi.unstubAllGlobals();
  });

  it("fetches /config.json, populates window.__ENV__, and returns the resolved config", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ PUBLIC_API_BASE: "https://fetched.example.com" }),
      }),
    );
    const config = await loadRuntimeConfig();
    expect(fetch).toHaveBeenCalledWith("/config.json");
    expect(window.__ENV__?.PUBLIC_API_BASE).toBe("https://fetched.example.com");
    expect(config.apiBase).toBe("https://fetched.example.com");
  });
});
