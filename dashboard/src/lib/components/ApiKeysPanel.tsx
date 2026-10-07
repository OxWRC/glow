import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApiKey, listApiKeys, revokeApiKey, type ApiKey } from "../api";
import { createI18n, type Locale } from "../i18n";

const TH =
  "px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs";

function errMsg(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export function ApiKeysPanel({
  token,
  locale,
  now,
}: {
  token: string;
  locale: Locale;
  now?: Date;
}) {
  const t = useMemo(() => createI18n(locale).t, [locale]);
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [revealKey, setRevealKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const revealRef = useRef<HTMLDialogElement>(null);

  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const revokeRef = useRef<HTMLDialogElement>(null);

  const reload = useCallback(async () => {
    try {
      setKeys(await listApiKeys(token));
      setLoadError(null);
    } catch (e: unknown) {
      setKeys(null);
      setLoadError(errMsg(e));
    }
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    const d = revealRef.current;
    if (!d) return;
    if (revealKey !== null && !d.open) d.showModal();
    else if (revealKey === null && d.open) d.close();
  }, [revealKey]);

  useEffect(() => {
    const d = revokeRef.current;
    if (!d) return;
    if (revokeTarget && !d.open) d.showModal();
    else if (!revokeTarget && d.open) d.close();
  }, [revokeTarget]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setFormError(null);
    try {
      const made = await createApiKey(token, {
        name,
        expires_in_days: Number(days),
      });
      setCopied(false);
      setRevealKey(made.key);
      setName("");
      setDays("90");
      await reload();
    } catch (err: unknown) {
      setFormError(errMsg(err));
    } finally {
      setCreating(false);
    }
  }

  async function copyKey() {
    if (revealKey === null) return;
    await navigator.clipboard.writeText(revealKey);
    setCopied(true);
  }

  async function confirmRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    setRevokeError(null);
    try {
      await revokeApiKey(token, revokeTarget.id);
      setRevokeTarget(null);
      await reload();
    } catch (err: unknown) {
      setRevokeError(errMsg(err));
    } finally {
      setRevoking(false);
    }
  }

  const today = now ?? new Date();
  const date = (iso: string) => new Date(iso).toLocaleDateString(locale);

  function status(k: ApiKey) {
    if (k.revoked_at) return { label: t("apiKeys.revoked"), cls: "badge-red" };
    if (new Date(k.expires_at) < today)
      return { label: t("apiKeys.expired"), cls: "badge-yellow" };
    return { label: t("apiKeys.active"), cls: "badge-green" };
  }

  return (
    <section aria-labelledby="api-keys-heading" className="space-y-4">
      <div>
        <h2 id="api-keys-heading" className="text-lg font-semibold">
          {t("apiKeys.title")}
        </h2>
        <p className="text-gray-500 mt-1">{t("apiKeys.subtitle")}</p>
      </div>

      <form
        onSubmit={handleCreate}
        className="card flex flex-wrap items-end gap-4"
      >
        <div>
          <label className="label" htmlFor="api-key-name">
            {t("apiKeys.name")}
          </label>
          <input
            id="api-key-name"
            type="text"
            className="input"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="api-key-days">
            {t("apiKeys.expiresInDays")}
          </label>
          <input
            id="api-key-days"
            type="number"
            className="input"
            min={1}
            max={365}
            required
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </div>
        <button type="submit" className="btn-primary" disabled={creating}>
          {creating ? t("apiKeys.creating") : t("apiKeys.create")}
        </button>
        {formError && (
          <p role="alert" className="w-full text-sm text-red-700">
            {formError}
          </p>
        )}
      </form>

      {keys === null ? (
        loadError ? (
          <p
            role="alert"
            className="card bg-red-50 border-red-200 text-red-700"
          >
            {t("apiKeys.loadFailed", { error: loadError })}
          </p>
        ) : (
          <p className="card text-gray-400">{t("apiKeys.loading")}</p>
        )
      ) : keys.length === 0 ? (
        <p className="card text-gray-400">{t("apiKeys.none")}</p>
      ) : (
        <div className="card p-0 overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                {(
                  [
                    "name",
                    "prefix",
                    "createdBy",
                    "created",
                    "expires",
                    "lastUsed",
                    "uses",
                    "status",
                    "actions",
                  ] as const
                ).map((h) => (
                  <th key={h} scope="col" className={TH}>
                    {t(`apiKeys.${h}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {keys.map((k) => {
                const s = status(k);
                return (
                  <tr key={k.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 font-medium text-gray-900">
                      {k.name}
                    </td>
                    <td className="px-6 py-4 font-mono">{k.prefix}…</td>
                    <td className="px-6 py-4">
                      {k.created_by ?? t("apiKeys.cli")}
                    </td>
                    <td className="px-6 py-4">{date(k.created_at)}</td>
                    <td className="px-6 py-4">{date(k.expires_at)}</td>
                    <td className="px-6 py-4">
                      {k.last_used_at
                        ? date(k.last_used_at)
                        : t("apiKeys.never")}
                    </td>
                    <td className="px-6 py-4">{k.use_count}</td>
                    <td className="px-6 py-4">
                      <span className={`badge ${s.cls}`}>{s.label}</span>
                    </td>
                    <td className="px-6 py-4">
                      {s.cls === "badge-green" && (
                        <button
                          type="button"
                          className="btn-danger btn-sm"
                          aria-label={`${t("apiKeys.revoke")} ${k.name}`}
                          onClick={() => {
                            setRevokeError(null);
                            setRevokeTarget(k);
                          }}
                        >
                          {t("apiKeys.revoke")}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <dialog
        ref={revealRef}
        aria-labelledby="api-key-reveal-title"
        onClose={() => setRevealKey(null)}
        className="rounded-2xl shadow-2xl w-full max-w-lg backdrop:bg-black/50 p-0"
      >
        <div className="bg-white rounded-2xl p-6 space-y-4">
          <h2
            id="api-key-reveal-title"
            className="text-lg font-semibold text-gray-900"
          >
            {t("apiKeys.revealTitle")}
          </h2>
          <p className="text-gray-600">{t("apiKeys.revealWarning")}</p>
          <code className="block break-all rounded bg-gray-100 p-3">
            {revealKey}
          </code>
          <div className="flex justify-end gap-3">
            <button type="button" className="btn-secondary" onClick={copyKey}>
              {copied ? t("apiKeys.copied") : t("apiKeys.copy")}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setRevealKey(null)}
            >
              {t("apiKeys.done")}
            </button>
          </div>
        </div>
      </dialog>

      <dialog
        ref={revokeRef}
        aria-labelledby="api-key-revoke-title"
        onClose={() => setRevokeTarget(null)}
        className="rounded-2xl shadow-2xl w-full max-w-sm backdrop:bg-black/50 p-0"
      >
        <div className="bg-white rounded-2xl p-6 space-y-4">
          <h2
            id="api-key-revoke-title"
            className="text-lg font-semibold text-gray-900"
          >
            {t("apiKeys.revokeTitle")}
          </h2>
          <p className="text-gray-600">
            {t("apiKeys.revokeConfirm", { name: revokeTarget?.name ?? "" })}
          </p>
          {revokeError && (
            <p role="alert" className="text-sm text-red-700">
              {revokeError}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setRevokeTarget(null)}
              disabled={revoking}
            >
              {t("apiKeys.cancel")}
            </button>
            <button
              type="button"
              className="btn-danger"
              onClick={confirmRevoke}
              disabled={revoking}
            >
              {revoking ? t("apiKeys.revoking") : t("apiKeys.revoke")}
            </button>
          </div>
        </div>
      </dialog>
    </section>
  );
}
