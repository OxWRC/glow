import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  exportAuditEvents,
  listAuditEvents,
  type AuditEvent,
  type AuditFilters,
} from "../api";
import { formatDateTime } from "../formatDateTime";
import { createI18n, type Locale } from "../i18n";

const TH =
  "px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs";
const PAGE = 100;

function errMsg(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

// <input type="datetime-local"> is zoneless local time; the API wants an instant.
function toIso(local: string) {
  return local ? new Date(local).toISOString() : undefined;
}

export function AuditLogPanel({
  token,
  locale,
}: {
  token: string;
  locale: Locale;
}) {
  const t = useMemo(() => createI18n(locale).t, [locale]);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Only the newest request may set state: an older one may be for stale
  // filters, a stale token, or a page a newer request supersedes.
  const latestRequest = useRef(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const load = useCallback(
    async (beforeId?: number) => {
      const request = ++latestRequest.current;
      setLoading(true);
      try {
        const page = await listAuditEvents(token, {
          ...filters,
          limit: PAGE,
          before_id: beforeId,
        });
        if (request !== latestRequest.current) return;
        setEvents((prev) => (beforeId && prev ? [...prev, ...page] : page));
        setHasMore(page.length === PAGE);
        setLoadError(null);
      } catch (e: unknown) {
        if (request === latestRequest.current) setLoadError(errMsg(e));
      } finally {
        if (request === latestRequest.current) setLoading(false);
      }
    },
    [token, filters],
  );

  useEffect(() => {
    setEvents(null);
    load();
  }, [load]);

  // Exports the applied filters (what the table shows), not unsubmitted edits.
  async function download() {
    setExporting(true);
    setExportError(null);
    try {
      const url = URL.createObjectURL(await exportAuditEvents(token, filters));
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-${new Date().toISOString().replace(/[:.]/g, "")}.jsonl`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      setExportError(errMsg(e));
    } finally {
      setExporting(false);
    }
  }

  function applyFilters(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const text = (k: string) => String(form.get(k) ?? "").trim() || undefined;
    setFilters({
      who: text("who"),
      what: text("what"),
      since: toIso(String(form.get("since") ?? "")),
      until: toIso(String(form.get("until") ?? "")),
    });
  }

  return (
    <section id="audit" aria-labelledby="audit-heading" className="space-y-4">
      <div>
        <h2 id="audit-heading" className="text-lg font-semibold">
          {t("audit.title")}
        </h2>
        <p className="text-gray-500 mt-1">{t("audit.subtitle")}</p>
      </div>

      <form
        role="search"
        aria-label={t("audit.filters")}
        className="card grid gap-4 sm:grid-cols-2 lg:grid-cols-4 items-end"
        onSubmit={applyFilters}
        onReset={() => setFilters({})}
      >
        <div>
          <label className="label" htmlFor="audit-who">
            {t("audit.who")}
          </label>
          <input
            id="audit-who"
            name="who"
            className="input"
            placeholder={t("audit.whoHint")}
          />
        </div>
        <div>
          <label className="label" htmlFor="audit-what">
            {t("audit.what")}
          </label>
          <input
            id="audit-what"
            name="what"
            className="input"
            placeholder={t("audit.whatHint")}
          />
        </div>
        <div>
          <label className="label" htmlFor="audit-since">
            {t("audit.since")}
          </label>
          <input
            id="audit-since"
            name="since"
            type="datetime-local"
            className="input"
          />
        </div>
        <div>
          <label className="label" htmlFor="audit-until">
            {t("audit.until")}
          </label>
          <input
            id="audit-until"
            name="until"
            type="datetime-local"
            className="input"
          />
        </div>
        <div className="flex gap-2 sm:col-span-2 lg:col-span-4 justify-end">
          <button
            type="button"
            className="btn-secondary mr-auto"
            onClick={download}
            disabled={exporting}
          >
            {exporting ? t("audit.exporting") : t("audit.export")}
          </button>
          <button type="reset" className="btn-secondary">
            {t("audit.clear")}
          </button>
          <button type="submit" className="btn-primary">
            {t("audit.apply")}
          </button>
        </div>
      </form>

      {exportError && (
        <p role="alert" className="card bg-red-50 border-red-200 text-red-700">
          {t("audit.exportFailed", { error: exportError })}
        </p>
      )}

      {loadError && (
        <p role="alert" className="card bg-red-50 border-red-200 text-red-700">
          {t("audit.loadFailed", { error: loadError })}
        </p>
      )}

      {events === null ? (
        !loadError && <p className="card text-gray-400">{t("audit.loading")}</p>
      ) : events.length === 0 ? (
        <p className="card text-gray-400">{t("audit.none")}</p>
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                {(["when", "who", "what", "status", "details"] as const).map(
                  (h) => (
                    <th key={h} scope="col" className={TH}>
                      {t(`audit.${h}`)}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {events.map((ev) => (
                <tr key={ev.id} className="align-top hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <time dateTime={ev.ts}>
                      {formatDateTime(ev.ts, locale)}
                    </time>
                  </td>
                  <td className="px-6 py-4 font-medium text-gray-900 break-all">
                    {ev.actor}
                  </td>
                  <td className="px-6 py-4 font-mono">{ev.action}</td>
                  <td className="px-6 py-4">{ev.status_code}</td>
                  <td className="px-6 py-4">
                    {ev.details && (
                      <details>
                        <summary className="cursor-pointer">
                          {t("audit.show")}
                        </summary>
                        <pre className="mt-2 text-xs whitespace-pre-wrap break-all">
                          {JSON.stringify(ev.details, null, 2)}
                        </pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasMore && events && (
        <div className="flex justify-center">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => load(events[events.length - 1].id)}
            disabled={loading}
          >
            {t("audit.loadMore")}
          </button>
        </div>
      )}
    </section>
  );
}
