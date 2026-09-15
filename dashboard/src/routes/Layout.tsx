import { useEffect, useMemo, useState } from "react";
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useAuth, useIsAdmin, useIsAuthenticated } from "../auth/AuthContext";
import {
  checkHealth,
  me,
  COGNITO_DOMAIN,
  COGNITO_CLIENT_ID,
  DEV_AUTH_BYPASS,
} from "../lib/api";
import { availableLocales, createI18n, type Locale } from "../lib/i18n";

type HealthStatus = "unknown" | "ok" | "down";

export function Layout() {
  const { token, identity, setIdentity, logout: clearAuth } = useAuth();
  const isAdmin = useIsAdmin();
  const isAuthenticated = useIsAuthenticated();
  const location = useLocation();
  const navigate = useNavigate();

  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);

  const [apiHealth, setApiHealth] = useState<HealthStatus>("unknown");
  const [apiVersion, setApiVersion] = useState<string | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    async function bootstrapIdentity() {
      try {
        const fetchedIdentity = await me(token ?? undefined);
        setIdentity(fetchedIdentity, token ?? undefined);
      } catch {
        // Fall back to anonymous
        setIdentity({ kind: "anonymous" });
      }
    }
    bootstrapIdentity();

    async function pollHealth() {
      const health = await checkHealth();
      if (!health) {
        setApiHealth("down");
        setApiVersion(null);
        return;
      }
      setApiVersion(health.version);
      setApiHealth("ok");
    }
    pollHealth();
    const healthInterval = setInterval(pollHealth, 30_000);
    return () => clearInterval(healthInterval);
    // Bootstrap/poll run once on mount only, same as the old onMount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function logout() {
    clearAuth();

    // Dev-bypass has no real Cognito session to end. With a real pool,
    // navigating straight to /login isn't a real sign-out: Cognito's own
    // hosted-UI SSO cookie survives, so the redirect-to-authorize that
    // /login's onMount does immediately re-authenticates the same user
    // without ever showing a login screen. Hit Cognito's hosted /logout
    // first (its logout_urls already allow /{locale}/login - see
    // deploy/aws/terraform/cognito.tf) so the next login actually prompts.
    if (DEV_AUTH_BYPASS || !COGNITO_DOMAIN || !COGNITO_CLIENT_ID) {
      navigate(`/${locale}/login`);
      return;
    }
    const logoutUri = `${window.location.origin}/${locale}/login`;
    const params = new URLSearchParams({
      client_id: COGNITO_CLIENT_ID,
      logout_uri: logoutUri,
    });
    window.location.href = `https://${COGNITO_DOMAIN}/logout?${params.toString()}`;
  }

  // Any path whose first segment isn't a known locale (e.g. /admin, matched
  // by the :locale route param itself) - redirect to /en<path> rather than
  // silently rendering with an invalid locale, matching the old
  // hooks.server.ts's catch-all default-locale redirect.
  if (!availableLocales.includes(localeParam as Locale)) {
    return (
      <Navigate to={`/en${location.pathname}${location.search}`} replace />
    );
  }

  const noChrome =
    location.pathname.endsWith("/login") ||
    location.pathname.includes("/auth/callback");

  if (noChrome) return <Outlet />;

  const displayName =
    identity?.kind === "authenticated" ? identity.username : null;
  const isHome = location.pathname === `/${locale}`;
  const isAdminRoute = location.pathname.startsWith(`/${locale}/admin`);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Navbar */}
      <nav className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between">
            <div className="flex items-center gap-8">
              <a href={`/${locale}`} className="flex items-center gap-2">
                <img src="/img/glow/glow-mark.png" alt="" className="h-8 w-8" />
                <span className="text-xl font-bold text-brand-700">GLOW</span>
                <span className="text-sm text-gray-400 hidden sm:block">
                  {i18n.t("nav.dashboard")}
                </span>
              </a>
              <div className="hidden md:flex items-center gap-1">
                <a
                  href={`/${locale}`}
                  className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isHome
                      ? "bg-blue-50 text-blue-700"
                      : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
                  }`}
                >
                  {i18n.t("nav.home")}
                </a>
                {isAdmin && (
                  <a
                    href={`/${locale}/admin`}
                    className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                      isAdminRoute
                        ? "bg-blue-50 text-blue-700"
                        : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
                    }`}
                  >
                    {i18n.t("nav.admin")}
                  </a>
                )}
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* API health indicator */}
              <span
                className={`hidden sm:flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full ${
                  apiHealth === "ok"
                    ? "bg-green-50 text-green-700"
                    : apiHealth === "down"
                      ? "bg-red-50 text-red-700"
                      : "bg-gray-50 text-gray-400"
                }`}
                title={
                  apiHealth === "ok"
                    ? `API v${apiVersion}`
                    : apiHealth === "down"
                      ? "API is unreachable"
                      : "Checking API status..."
                }
              >
                {apiHealth === "ok" ? (
                  <>
                    <span className="h-1.5 w-1.5 rounded-full bg-green-500 inline-block" />
                    {i18n.t("nav.api")}
                  </>
                ) : apiHealth === "down" ? (
                  <>
                    {/* Unplugged icon */}
                    <svg
                      className="h-3.5 w-3.5"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      aria-hidden="true"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M18.364 5.636a9 9 0 010 12.728M15.536 8.464a5 5 0 010 7.072M9 9l6 6M3 3l18 18"
                      />
                    </svg>
                    {i18n.t("nav.apiDown")}
                  </>
                ) : (
                  <>
                    <span className="h-1.5 w-1.5 rounded-full bg-gray-300 inline-block motion-safe:animate-pulse" />
                    {i18n.t("nav.api")}
                  </>
                )}
              </span>

              {displayName && (
                <div className="hidden md:flex items-center gap-2">
                  <span className="text-sm text-gray-500">{displayName}</span>
                  {isAdmin && (
                    <span className="badge badge-blue">
                      {i18n.t("nav.adminBadge")}
                    </span>
                  )}
                </div>
              )}
              {isAuthenticated ? (
                <button className="btn-secondary btn-sm" onClick={logout}>
                  {i18n.t("nav.signOut")}
                </button>
              ) : (
                <a href={`/${locale}/login`} className="btn-primary btn-sm">
                  {i18n.t("nav.signIn")}
                </a>
              )}
              <button
                className="md:hidden p-2 rounded-md text-gray-500 hover:bg-gray-100"
                onClick={() => setMobileMenuOpen((open) => !open)}
                aria-label="Menu"
              >
                <svg
                  className="h-5 w-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>
            </div>
          </div>
        </div>

        {/* Mobile menu */}
        {mobileMenuOpen && (
          <div className="md:hidden border-t border-gray-200 px-4 py-3 space-y-1">
            <a
              href={`/${locale}`}
              className="block px-3 py-2 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-100"
              onClick={() => setMobileMenuOpen(false)}
            >
              {i18n.t("nav.home")}
            </a>
            {isAdmin && (
              <a
                href={`/${locale}/admin`}
                className="block px-3 py-2 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-100"
                onClick={() => setMobileMenuOpen(false)}
              >
                {i18n.t("nav.admin")}
              </a>
            )}
            {displayName && (
              <div className="px-3 py-2 text-sm text-gray-500">
                {displayName}
              </div>
            )}
            {/* API health in mobile menu */}
            <div className="px-3 py-2 text-xs text-gray-400">
              {i18n.t("nav.api")}:{" "}
              {apiHealth === "ok"
                ? `${i18n.t("nav.online")} (v${apiVersion})`
                : apiHealth === "down"
                  ? i18n.t("nav.offline")
                  : i18n.t("nav.checking")}
            </div>
          </div>
        )}
      </nav>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet />
      </main>
    </div>
  );
}
