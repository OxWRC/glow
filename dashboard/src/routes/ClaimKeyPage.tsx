import { useEffect, useMemo } from "react";
import { useLocation, useParams } from "react-router-dom";
import { ApiKeyClaim } from "../lib/components/ApiKeyClaim";
import { availableLocales, createI18n, type Locale } from "../lib/i18n";

/** Public: the emailed claim link lands here, token in the URL fragment so
 * it never reaches server logs or Referer headers. */
export function ClaimKeyPage() {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const { hash } = useLocation();
  const claimToken = new URLSearchParams(hash.slice(1)).get("token");

  useEffect(() => {
    document.title = `${i18n.t("claimKey.title")} — ${i18n.t("login.title")} ${i18n.t("nav.dashboard")}`;
  }, [i18n]);

  return <ApiKeyClaim claimToken={claimToken} locale={locale} />;
}
