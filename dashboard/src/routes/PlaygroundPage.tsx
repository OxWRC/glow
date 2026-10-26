import { useEffect, useMemo } from "react";
import { useParams } from "react-router-dom";
import { ApiKeyTester } from "../lib/components/ApiKeyTester";
import { availableLocales, createI18n, type Locale } from "../lib/i18n";
import { getRuntimeConfig } from "../runtimeConfig";

/** Demo mode only (App.tsx registers the route only then). Needs no login:
 * the tester authenticates with the pasted API key alone. */
export function PlaygroundPage() {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const { mailViewerUrl } = getRuntimeConfig();

  useEffect(() => {
    document.title = `${i18n.t("playground.title")} — ${i18n.t("login.title")} ${i18n.t("nav.dashboard")}`;
  }, [i18n]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{i18n.t("playground.title")}</h1>
        <p className="text-gray-500 mt-1">{i18n.t("playground.intro")}</p>
        {mailViewerUrl && (
          <a
            href={mailViewerUrl}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary btn-sm mt-3 inline-block"
          >
            {i18n.t("playground.inbox")}
          </a>
        )}
      </div>
      <ApiKeyTester locale={locale} />
    </div>
  );
}
