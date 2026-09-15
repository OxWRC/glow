import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { exchangeCodeForToken, me } from "../lib/api";
import { useAuth } from "../auth/AuthContext";
import { createI18n, availableLocales, type Locale } from "../lib/i18n";

export function AuthCallbackPage() {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const [searchParams] = useSearchParams();
  const { setIdentity } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = `${i18n.t("login.signingIn")} — ${i18n.t("login.title")}`;
  }, [i18n]);

  useEffect(() => {
    async function run() {
      // Verify state before anything else, including the error branch below -
      // Cognito echoes state back on both success and error redirects.
      const returnedState = searchParams.get("state");
      const expectedState = sessionStorage.getItem("oauth_state");
      sessionStorage.removeItem("oauth_state");
      if (!expectedState || returnedState !== expectedState) {
        setError(i18n.t("login.loginFailed"));
        return;
      }

      const oauthError = searchParams.get("error");
      if (oauthError) {
        setError(searchParams.get("error_description") ?? oauthError);
        return;
      }

      const code = searchParams.get("code");
      const verifier = sessionStorage.getItem("pkce_code_verifier");
      sessionStorage.removeItem("pkce_code_verifier");

      if (!code || !verifier) {
        setError(i18n.t("login.loginFailed"));
        return;
      }

      try {
        const redirectUri = `${window.location.origin}/${locale}/auth/callback`;
        const tokens = await exchangeCodeForToken(code, redirectUri, verifier);
        const identity = await me(tokens.id_token);
        if (identity.kind !== "authenticated") {
          setError(i18n.t("login.loginFailed"));
          return;
        }
        setIdentity(identity, tokens.id_token);
        navigate(`/${locale}`);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : i18n.t("login.loginFailed"));
      }
    }
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen bg-blue-900 flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="bg-white rounded-2xl shadow-xl p-8 text-center">
          {error ? (
            <>
              <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700 mb-4">
                {error}
              </div>
              <a
                href={`/${locale}/login`}
                className="btn-primary inline-block py-2.5 px-6"
              >
                {i18n.t("login.backToLogin")}
              </a>
            </>
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
              <span className="text-gray-600">{i18n.t("login.signingIn")}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
