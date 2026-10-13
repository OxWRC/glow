import { useMemo, useState } from "react";
import {
  API_KEY_ENDPOINTS,
  apiKeyEndpointUrl,
  fetchWithApiKey,
  tryApiKey,
  type ApiKeyEndpoint,
  type ApiKeyTryResult,
} from "../api";
import { createI18n, type Locale } from "../i18n";

// Rendering a multi-megabyte <pre> locks the page; the downloads carry the rest.
const MAX_SHOWN = 20_000;

const ENDPOINTS = Object.keys(API_KEY_ENDPOINTS) as ApiKeyEndpoint[];

function errMsg(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

function saveJson(data: unknown, filename: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Demo-mode only: a tiny Swagger for the API-key endpoints. */
export function ApiKeyTester({ locale }: { locale: Locale }) {
  const t = useMemo(() => createI18n(locale).t, [locale]);
  const [key, setKey] = useState("");
  const [endpoint, setEndpoint] = useState<ApiKeyEndpoint>("export");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<ApiKeyTryResult | null>(null);
  const [downloading, setDownloading] = useState<ApiKeyEndpoint | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setSending(true);
    setError(null);
    setResult(null);
    try {
      setResult(await tryApiKey(key.trim(), endpoint));
    } catch (err: unknown) {
      setError(errMsg(err));
    } finally {
      setSending(false);
    }
  }

  async function download(which: ApiKeyEndpoint) {
    setDownloading(which);
    setError(null);
    try {
      saveJson(await fetchWithApiKey(key.trim(), which), `glow-${which}.json`);
    } catch (err: unknown) {
      setError(errMsg(err));
    } finally {
      setDownloading(null);
    }
  }

  const ok = result !== null && result.status < 400;

  return (
    <section
      id="key-tester"
      aria-labelledby="key-tester-heading"
      className="space-y-4"
    >
      <div>
        <h2 id="key-tester-heading" className="text-lg font-semibold">
          {t("keyTester.title")}
        </h2>
        <p className="text-gray-500 mt-1">{t("keyTester.subtitle")}</p>
      </div>

      <form onSubmit={send} className="card space-y-4">
        <div>
          <label className="label" htmlFor="key-tester-key">
            {t("keyTester.key")}
          </label>
          <input
            id="key-tester-key"
            type="text"
            className="input font-mono"
            autoComplete="off"
            spellCheck={false}
            required
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </div>

        <fieldset className="space-y-2">
          <legend className="label">{t("keyTester.endpoint")}</legend>
          {ENDPOINTS.map((ep) => (
            <label key={ep} className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="key-tester-endpoint"
                value={ep}
                checked={endpoint === ep}
                onChange={() => setEndpoint(ep)}
              />
              <code className="text-sm">GET {API_KEY_ENDPOINTS[ep]}</code>
              <span className="text-sm text-gray-500">
                {t(`keyTester.endpoints.${ep}`)}
              </span>
            </label>
          ))}
        </fieldset>

        <button type="submit" className="btn-primary" disabled={sending}>
          {sending ? t("keyTester.sending") : t("keyTester.send")}
        </button>

        <div>
          <h3 className="label">{t("keyTester.curl")}</h3>
          <code className="block break-all rounded bg-gray-100 p-3 text-sm">
            {`curl -H "X-API-Key: ${key.trim() ? `${key.trim().slice(0, 12)}…` : "<key>"}" ${apiKeyEndpointUrl(endpoint)}`}
          </code>
        </div>
      </form>

      {error && (
        <p role="alert" className="card bg-red-50 border-red-200 text-red-700">
          {t("keyTester.failed", { error })}
        </p>
      )}

      {result && (
        <section
          aria-labelledby="key-tester-response-heading"
          className="card space-y-3"
        >
          <div className="flex flex-wrap items-center gap-3">
            <h3 id="key-tester-response-heading" className="font-semibold">
              {t("keyTester.response")}
            </h3>
            <span className={`badge ${ok ? "badge-green" : "badge-red"}`}>
              {t("keyTester.status", {
                status: result.status,
                statusText: result.statusText,
                ms: result.ms,
              })}
            </span>
          </div>
          {result.body.length > MAX_SHOWN && (
            <p className="text-sm text-gray-500">
              {t("keyTester.truncated", {
                shown: MAX_SHOWN.toLocaleString(locale),
                total: result.body.length.toLocaleString(locale),
              })}
            </p>
          )}
          <pre
            tabIndex={0}
            aria-label={t("keyTester.responseBody")}
            className="max-h-96 overflow-auto rounded bg-gray-100 p-3 text-xs"
          >
            {result.body.slice(0, MAX_SHOWN)}
          </pre>
        </section>
      )}

      <div className="card space-y-3">
        <div>
          <h3 className="font-semibold">{t("keyTester.downloads")}</h3>
          <p className="text-sm text-gray-500">
            {t("keyTester.downloadsHint")}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          {(["export", "raw"] as const).map((which) => (
            <button
              key={which}
              type="button"
              className="btn-secondary"
              disabled={!key.trim() || downloading !== null}
              onClick={() => download(which)}
            >
              {downloading === which
                ? t("keyTester.downloading")
                : t(
                    which === "export"
                      ? "keyTester.downloadExport"
                      : "keyTester.downloadRaw",
                  )}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
