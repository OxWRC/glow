import { useMemo } from "react";
import type { PeriodCounts } from "../api";
import { createI18n, type Locale } from "../i18n";

/**
 * Shown instead of the query builder until the first vintage is cut: nothing
 * is queryable before then. Counts are live (unpublished) and already floored
 * by the API, so a null means "too few to show", never a hidden number.
 */
export function AwaitingDataNotice({
  locale,
  isAdmin,
  counts,
  schoolName,
}: {
  locale: Locale;
  isAdmin: boolean;
  /** null when the counts couldn't be fetched. */
  counts: PeriodCounts | null;
  /** Scope of the counts; null for dataset-wide. */
  schoolName: string | null;
}) {
  const i18n = useMemo(() => createI18n(locale), [locale]);
  const t = i18n.t;
  const numberFormat = useMemo(() => new Intl.NumberFormat(locale), [locale]);
  const periods = counts ? Object.keys(counts).sort() : [];

  return (
    <section
      aria-labelledby="awaiting-data-heading"
      className="bg-white rounded-lg shadow-sm p-6 border-l-4 border-blue-600"
    >
      <h2
        id="awaiting-data-heading"
        className="text-xl font-semibold text-gray-900"
      >
        {t("awaitingData.title")}
      </h2>
      <p className="text-gray-700 mt-2">
        {isAdmin ? t("awaitingData.adminBody") : t("awaitingData.body")}
      </p>
      {isAdmin && (
        <p className="mt-3">
          <a href={`/${locale}/admin#vintages`} className="btn-primary btn-sm">
            {t("awaitingData.adminAction")}
          </a>
        </p>
      )}

      {counts === null ? (
        <p className="text-sm text-gray-500 mt-6">
          {t("awaitingData.countsUnavailable")}
        </p>
      ) : periods.length === 0 ? (
        <p className="text-sm text-gray-500 mt-6">
          {t("awaitingData.noResponses")}
        </p>
      ) : (
        <table className="mt-6 text-left text-sm">
          <caption className="text-left font-medium text-gray-900 mb-2">
            {schoolName
              ? t("awaitingData.countsCaptionSchool", { school: schoolName })
              : t("awaitingData.countsCaptionAll")}
          </caption>
          <thead>
            <tr className="text-gray-500">
              <th scope="col" className="pr-8 font-normal">
                {t("awaitingData.period")}
              </th>
              <th scope="col" className="font-normal">
                {t("awaitingData.students")}
              </th>
            </tr>
          </thead>
          <tbody>
            {periods.map((period) => {
              const n = counts[period];
              return (
                <tr key={period}>
                  <th scope="row" className="pr-8 font-normal text-gray-900">
                    {period}
                  </th>
                  <td className="text-gray-900">
                    {n === null ? (
                      <span className="text-gray-500">
                        {t("awaitingData.tooFew")}
                      </span>
                    ) : (
                      numberFormat.format(n)
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {counts !== null && periods.length > 0 && (
        <p className="text-xs text-gray-500 mt-2">
          {t("awaitingData.countsNote")}
        </p>
      )}
    </section>
  );
}
