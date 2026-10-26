import { useMemo, useState } from "react";
import {
  API_KEY_SELF_URL,
  ApiError,
  claimApiKey,
  type ApiKeyClaimed,
} from "../api";
import { formatDateTime } from "../formatDateTime";
import { createI18n, type Locale } from "../i18n";

/** One-time reveal of an emailed API key. Claims only on click: mail
 * scanners prefetch links, so loading the page must never spend the token. */
export function ApiKeyClaim({
  claimToken,
  locale,
}: {
  claimToken: string | null;
  locale: Locale;
}) {
  const t = useMemo(() => createI18n(locale).t, [locale]);
  const [claiming, setClaiming] = useState(false);
  const [claimed, setClaimed] = useState<ApiKeyClaimed | null>(null);
  const [gone, setGone] = useState(!claimToken);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  async function reveal() {
    if (!claimToken) return;
    setClaiming(true);
    setError(null);
    try {
      setClaimed(await claimApiKey(claimToken));
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 410) setGone(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClaiming(false);
    }
  }

  async function copy() {
    if (!claimed) return;
    try {
      await navigator.clipboard.writeText(claimed.key);
      setCopied(true);
    } catch {
      setCopyFailed(true);
    }
  }

  return (
    <section
      aria-labelledby="claim-key-heading"
      className="card max-w-2xl space-y-4"
    >
      <h1 id="claim-key-heading" className="text-xl font-semibold">
        {t("claimKey.title")}
      </h1>

      {gone ? (
        <p role="alert" className="text-red-700">
          {t("claimKey.invalid")}
        </p>
      ) : claimed ? (
        <>
          <p role="alert" className="font-medium text-gray-900">
            {t("claimKey.warning")}
          </p>
          <div>
            <h2 className="label">{t("claimKey.key")}</h2>
            <code className="block break-all rounded-sm bg-gray-100 p-3">
              {claimed.key}
            </code>
          </div>
          <p className="text-sm text-gray-600">
            {claimed.expires_at
              ? t("claimKey.expires", {
                  date: formatDateTime(claimed.expires_at, locale),
                })
              : t("claimKey.neverExpires")}
          </p>
          <button type="button" className="btn-primary" onClick={copy}>
            {copied ? t("claimKey.copied") : t("claimKey.copy")}
          </button>
          {copyFailed && (
            <p role="alert" className="text-sm text-red-700">
              {t("claimKey.copyFailed")}
            </p>
          )}
          <div>
            <h2 className="font-semibold">{t("claimKey.revokeTitle")}</h2>
            <p className="text-sm text-gray-600">{t("claimKey.revokeHint")}</p>
            <code className="mt-2 block break-all rounded-sm bg-gray-100 p-3 text-sm">
              {`curl -X DELETE -H "X-API-Key: ${claimed.key}" ${API_KEY_SELF_URL}`}
            </code>
          </div>
        </>
      ) : (
        <>
          <p className="text-gray-600">{t("claimKey.intro")}</p>
          <button
            type="button"
            className="btn-primary"
            disabled={claiming}
            onClick={reveal}
          >
            {claiming ? t("claimKey.revealing") : t("claimKey.reveal")}
          </button>
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {t("claimKey.failed", { error })}
            </p>
          )}
        </>
      )}
    </section>
  );
}
