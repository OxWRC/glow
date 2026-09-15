import { useRouteError } from "react-router-dom";
import { useParams } from "react-router-dom";
import { availableLocales, createI18n, type Locale } from "../lib/i18n";

interface ErrorPageProps {
  status?: number;
  message?: string;
}

export function ErrorPage({ status, message }: ErrorPageProps) {
  // On the router's errorElement, React Router gives us the thrown error
  // instead of props; on the "*" catch-all route we get explicit props.
  const routeError = useRouteError();
  const errorStatus =
    status ??
    (routeError && typeof routeError === "object" && "status" in routeError
      ? (routeError as { status?: number }).status
      : undefined) ??
    500;
  const errorMessage =
    message ??
    (routeError instanceof Error ? routeError.message : undefined) ??
    "An unexpected error occurred";

  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = createI18n(locale);

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="max-w-md w-full text-center">
        <h1 className="text-6xl font-bold text-gray-900 mb-4">{errorStatus}</h1>
        <h2 className="text-2xl font-semibold text-gray-700 mb-4">
          {errorStatus === 404 ? "Page Not Found" : "Something went wrong"}
        </h2>
        <p className="text-gray-600 mb-8">{errorMessage}</p>
        <div className="space-y-4">
          <a
            href={`/${locale}`}
            className="inline-block px-6 py-3 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            {i18n.t("nav.home")}
          </a>
          <button
            onClick={() => window.location.reload()}
            className="block w-full px-6 py-3 bg-gray-200 text-gray-900 font-medium rounded-lg hover:bg-gray-300 transition-colors"
          >
            Reload Page
          </button>
        </div>
      </div>
    </main>
  );
}
