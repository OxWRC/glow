import { DEMO_MODE, OIDC_CLIENT_ID, OIDC_ISSUER } from "./api";
import { getOidcEndpoints } from "./oidc";

/**
 * Ends the provider's SSO session by navigating to its end_session_endpoint,
 * returning to /<locale>/login afterwards. Returns false (no navigation) when
 * that isn't possible - demo mode, no config, no end_session_endpoint, or
 * discovery failure - so the caller can fall back to a local-only sign-out.
 *
 * Navigating straight to /login isn't a real sign-out: the provider's SSO
 * session survives and /login would immediately re-authenticate the same user.
 */
export async function endProviderSession(locale: string): Promise<boolean> {
  if (DEMO_MODE || !OIDC_ISSUER || !OIDC_CLIENT_ID) return false;
  try {
    const { end_session_endpoint } = await getOidcEndpoints(OIDC_ISSUER);
    if (!end_session_endpoint) return false;
    const params = new URLSearchParams({
      client_id: OIDC_CLIENT_ID,
      post_logout_redirect_uri: `${window.location.origin}/${locale}/login`,
    });
    window.location.href = `${end_session_endpoint}?${params.toString()}`;
    return true;
  } catch {
    return false;
  }
}
