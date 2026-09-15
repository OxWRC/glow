import { describe, it, expect, afterEach } from "vitest";
import { getRuntimeConfig } from "./runtimeConfig";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).window ??= {};

describe("getRuntimeConfig", () => {
  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).__ENV__;
  });

  it("reads values from window.__ENV__ when present", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__ENV__ = {
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
