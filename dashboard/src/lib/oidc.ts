export interface OidcEndpoints {
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

const cache = new Map<string, Promise<OidcEndpoints>>();

const normalize = (issuer: string) => issuer.replace(/\/+$/, "");

async function discover(issuer: string): Promise<OidcEndpoints> {
  const base = normalize(issuer);
  const res = await fetch(`${base}/.well-known/openid-configuration`);
  if (!res.ok) throw new Error(`OIDC discovery failed (${res.status})`);
  const doc = (await res.json()) as Partial<OidcEndpoints> & {
    issuer?: string;
  };
  if (!doc.issuer || normalize(doc.issuer) !== base) {
    throw new Error("OIDC discovery issuer mismatch");
  }
  if (!doc.authorization_endpoint || !doc.token_endpoint) {
    throw new Error("OIDC discovery document is missing endpoints");
  }
  return {
    authorization_endpoint: doc.authorization_endpoint,
    token_endpoint: doc.token_endpoint,
    end_session_endpoint: doc.end_session_endpoint,
  };
}

/** Discovery document endpoints for the issuer, fetched once per page load. */
export function getOidcEndpoints(issuer: string): Promise<OidcEndpoints> {
  const key = normalize(issuer);
  let p = cache.get(key);
  if (!p) {
    p = discover(issuer);
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return p;
}
