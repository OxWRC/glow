import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  devLogin,
  me,
  ApiError,
  DEV_AUTH_BYPASS,
  COGNITO_DOMAIN,
  COGNITO_CLIENT_ID,
} from "../lib/api";
import { useAuth } from "../auth/AuthContext";
import { createI18n, availableLocales, type Locale } from "../lib/i18n";

function base64url(bytes: Uint8Array): string {
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Shared by the PKCE verifier and the CSRF `state` value - both are just
// "a random, unguessable token", no need for two generators.
function randomToken(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

async function generateCodeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64url(new Uint8Array(digest));
}

export function LoginPage() {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const { setIdentity } = useAuth();
  const navigate = useNavigate();

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [schoolIdInput, setSchoolIdInput] = useState("");

  useEffect(() => {
    document.title = `${i18n.t("login.signIn")} — ${i18n.t("login.title")} ${i18n.t("nav.dashboard")}`;
  }, [i18n]);

  useEffect(() => {
    async function redirectToCognito() {
      if (!COGNITO_DOMAIN || !COGNITO_CLIENT_ID) {
        setError(i18n.t("login.configError"));
        return;
      }
      const verifier = randomToken();
      sessionStorage.setItem("pkce_code_verifier", verifier);
      const state = randomToken();
      sessionStorage.setItem("oauth_state", state);
      const challenge = await generateCodeChallenge(verifier);
      const redirectUri = `${window.location.origin}/${locale}/auth/callback`;
      const params = new URLSearchParams({
        client_id: COGNITO_CLIENT_ID,
        response_type: "code",
        scope: "openid email profile",
        redirect_uri: redirectUri,
        code_challenge: challenge,
        code_challenge_method: "S256",
        state,
      });
      window.location.href = `https://${COGNITO_DOMAIN}/oauth2/authorize?${params.toString()}`;
    }

    if (!DEV_AUTH_BYPASS) {
      redirectToCognito().catch((e: unknown) => {
        setError(e instanceof Error ? e.message : i18n.t("login.loginFailed"));
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loginAs(role: "admin" | "wrc" | "school") {
    setError(null);
    setLoading(true);
    try {
      const trimmed = schoolIdInput.trim();
      const schoolId = role === "school" && trimmed ? Number(trimmed) : undefined;
      const token = await devLogin(role, schoolId);
      const identity = await me(token.access_token);
      if (identity.kind !== "authenticated") {
        setError(i18n.t("login.loginFailed"));
        return;
      }
      setIdentity(identity, token.access_token);
      navigate(`/${locale}`);
    } catch (e: unknown) {
      setError(
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : i18n.t("login.loginFailed"),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-blue-900 flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-white">{i18n.t("login.title")}</h1>
          <p className="text-blue-200 mt-1">{i18n.t("login.subtitle")}</p>
        </div>

        <div className="bg-white rounded-2xl shadow-xl p-8">
          {DEV_AUTH_BYPASS ? (
            <>
              <h2 className="text-xl font-semibold text-gray-800 mb-6">
                {i18n.t("login.devPickerTitle")}
              </h2>

              <div className="space-y-4">
                <div>
                  <label className="label" htmlFor="school-id">
                    {i18n.t("login.schoolIdLabel")}
                  </label>
                  <input
                    id="school-id"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    className="input"
                    value={schoolIdInput}
                    onChange={(e) => setSchoolIdInput(e.target.value)}
                    disabled={loading}
                    placeholder={i18n.t("login.schoolIdPlaceholder")}
                  />
                </div>

                {error && (
                  <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
                    {error}
                  </div>
                )}

                <div className="grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    className="btn-primary justify-center py-2.5"
                    disabled={loading}
                    onClick={() => loginAs("admin")}
                  >
                    {i18n.t("login.devAdmin")}
                  </button>
                  <button
                    type="button"
                    className="btn-primary justify-center py-2.5"
                    disabled={loading}
                    onClick={() => loginAs("wrc")}
                  >
                    {i18n.t("login.devWrc")}
                  </button>
                  <button
                    type="button"
                    className="btn-primary justify-center py-2.5"
                    disabled={loading}
                    onClick={() => loginAs("school")}
                  >
                    {i18n.t("login.devSchool")}
                  </button>
                </div>
              </div>
            </>
          ) : error ? (
            <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
              {error}
            </div>
          ) : (
            <div className="flex items-center justify-center gap-3 py-4">
              <svg
                className="motion-safe:animate-spin h-5 w-5 text-blue-700"
                fill="none"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                ></circle>
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v8H4z"
                ></path>
              </svg>
              <span className="text-gray-600">{i18n.t("login.redirecting")}</span>
            </div>
          )}
        </div>

        <p className="text-center text-blue-200 text-xs mt-6">
          {i18n.t("login.footer")}
        </p>
      </div>
    </div>
  );
}
