import { describe, it, expect, afterEach } from "vitest";
import { getRuntimeConfig } from "./runtimeConfig";

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
