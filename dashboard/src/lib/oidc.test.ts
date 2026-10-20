import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const issuer = "https://idp.example.test/realm";
const doc = {
  issuer,
  authorization_endpoint: `${issuer}/authorize`,
  token_endpoint: `${issuer}/token`,
  end_session_endpoint: `${issuer}/logout`,
  jwks_uri: `${issuer}/jwks`,
};

function stubFetch(body: unknown) {
  const f = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
  vi.stubGlobal("fetch", f);
  return f;
}

describe("getOidcEndpoints", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the discovery document and returns the endpoints", async () => {
    const f = stubFetch(doc);
    const { getOidcEndpoints } = await import("./oidc");
    expect(await getOidcEndpoints(issuer)).toEqual({
      authorization_endpoint: doc.authorization_endpoint,
      token_endpoint: doc.token_endpoint,
      end_session_endpoint: doc.end_session_endpoint,
    });
    expect(f).toHaveBeenCalledWith(
      `${issuer}/.well-known/openid-configuration`,
    );
  });

  it("handles a trailing slash on the issuer", async () => {
    const f = stubFetch({ ...doc, issuer: `${issuer}/` });
    const { getOidcEndpoints } = await import("./oidc");
    await getOidcEndpoints(`${issuer}/`);
    expect(f).toHaveBeenCalledWith(
      `${issuer}/.well-known/openid-configuration`,
    );
  });

  it("caches per page load", async () => {
    const f = stubFetch(doc);
    const { getOidcEndpoints } = await import("./oidc");
    await getOidcEndpoints(issuer);
    await getOidcEndpoints(issuer);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("rejects when the document issuer differs", async () => {
    stubFetch({ ...doc, issuer: "https://evil.example.test" });
    const { getOidcEndpoints } = await import("./oidc");
    await expect(getOidcEndpoints(issuer)).rejects.toThrow(/issuer/i);
  });
});
