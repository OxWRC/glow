import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cutVintage, listVintages, type Vintage } from "../api";
import { createI18n, type Locale } from "../i18n";

const TH =
  "px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs";

function errMsg(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export function VintagesPanel({
  token,
  locale,
}: {
  token: string;
  locale: Locale;
}) {
  const t = useMemo(() => createI18n(locale).t, [locale]);
  const [vintages, setVintages] = useState<Vintage[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [cutting, setCutting] = useState(false);
  const [cutError, setCutError] = useState<string | null>(null);
  const confirmRef = useRef<HTMLDialogElement>(null);

  const reload = useCallback(async () => {
    try {
      setVintages(await listVintages(token));
      setLoadError(null);
    } catch (e: unknown) {
      setVintages(null);
      setLoadError(errMsg(e));
    }
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    const d = confirmRef.current;
    if (!d) return;
    if (confirming && !d.open) d.showModal();
    else if (!confirming && d.open) d.close();
  }, [confirming]);

  async function confirmCut() {
    setCutting(true);
    setCutError(null);
    try {
      await cutVintage(token);
      setConfirming(false);
      await reload();
    } catch (err: unknown) {
      setCutError(errMsg(err));
    } finally {
      setCutting(false);
    }
  }

  const date = (iso: string) => new Date(iso).toLocaleString(locale);

  return (
    <section
      id="vintages"
      aria-labelledby="vintages-heading"
      className="space-y-4"
    >
      <div>
        <h2 id="vintages-heading" className="text-lg font-semibold">
          {t("vintages.title")}
        </h2>
        <p className="text-gray-500 mt-1">{t("vintages.subtitle")}</p>
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setCutError(null);
            setConfirming(true);
          }}
        >
          {t("vintages.cutNow")}
        </button>
      </div>

      {vintages === null ? (
        loadError ? (
          <p
            role="alert"
            className="card bg-red-50 border-red-200 text-red-700"
          >
            {t("vintages.loadFailed", { error: loadError })}
          </p>
        ) : (
          <p className="card text-gray-400">{t("vintages.loading")}</p>
        )
      ) : vintages.length === 0 ? (
        <p className="card text-gray-400">{t("vintages.none")}</p>
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                {(
                  ["created", "createdBy", "rows", "datasetVersion"] as const
                ).map((h) => (
                  <th key={h} scope="col" className={TH}>
                    {t(`vintages.${h}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {[...vintages].reverse().map((v) => (
                <tr key={v.prefix} className="hover:bg-gray-50">
                  <td className="px-6 py-4">{date(v.created_at)}</td>
                  <td className="px-6 py-4">
                    {v.created_by_username ?? t("vintages.scheduler")}
                  </td>
                  <td className="px-6 py-4">{v.row_count}</td>
                  <td className="px-6 py-4 font-mono">{v.dataset_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <dialog
        ref={confirmRef}
        aria-labelledby="vintage-cut-title"
        onClose={() => setConfirming(false)}
        className="rounded-2xl shadow-2xl w-full max-w-sm backdrop:bg-black/50 p-0"
      >
        <div className="bg-white rounded-2xl p-6 space-y-4">
          <h2
            id="vintage-cut-title"
            className="text-lg font-semibold text-gray-900"
          >
            {t("vintages.cutTitle")}
          </h2>
          <p className="text-gray-600">{t("vintages.cutConfirm")}</p>
          {cutError && (
            <p role="alert" className="text-sm text-red-700">
              {t("vintages.cutFailed", { error: cutError })}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setConfirming(false)}
              disabled={cutting}
            >
              {t("vintages.cancel")}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={confirmCut}
              disabled={cutting}
            >
              {cutting ? t("vintages.cutting") : t("vintages.cutNow")}
            </button>
          </div>
        </div>
      </dialog>
    </section>
  );
}
