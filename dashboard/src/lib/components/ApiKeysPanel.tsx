import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createApiKey,
  listApiKeys,
  regenerateApiKey,
  revokeApiKey,
  type ApiKey,
  type ApiKeyStatus,
} from "../api";
import { formatDateTime } from "../formatDateTime";
import { createI18n, type Locale } from "../i18n";

const TH =
  "px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs";

const BADGE: Record<ApiKeyStatus, string> = {
  unclaimed: "badge-blue",
  active: "badge-green",
  expired: "badge-yellow",
  revoked: "badge-red",
};

function errMsg(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export function ApiKeysPanel({
  token,
  locale,
}: {
  token: string;
  locale: Locale;
}) {
  const t = useMemo(() => createI18n(locale).t, [locale]);
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [days, setDays] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setFormError(null);
    setSentTo(null);
    try {
      const made = await createApiKey(token, {
        email,
        name: name.trim() || null,
        notes: notes.trim() || null,
        expires_in_days: days ? Number(days) : null,
      });
      setSentTo(made.email);
      setEmail("");
      setName("");
      setNotes("");
      setDays("");
      await reload();
    } catch (err: unknown) {
      setFormError(errMsg(err));
    } finally {
      setCreating(false);
    }
  }

  const label = (k: ApiKey) => k.name ?? k.email ?? t("apiKeys.unnamed");

  async function act(k: ApiKey, action: "revoke" | "regenerate") {
    const message =
      action === "revoke"
        ? t("apiKeys.revokeConfirm", { name: label(k) })
        : t("apiKeys.regenerateConfirm", {
            name: label(k),
            email: k.email ?? "",
          });
    if (!window.confirm(message)) return;
    setBusyId(k.id);
    setActionError(null);
    setSentTo(null);
    try {
      if (action === "revoke") {
        await revokeApiKey(token, k.id);
      } else {
        setSentTo((await regenerateApiKey(token, k.id)).email);
      }
      await reload();
    } catch (err: unknown) {
      setActionError(errMsg(err));
    } finally {
      setBusyId(null);
    }
  }

  const date = (iso: string | null) =>
    iso ? formatDateTime(iso, locale) : t("apiKeys.never");

  return (
    <section
      id="api-keys"
      aria-labelledby="api-keys-heading"
      className="space-y-4"
    >
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
          <label className="label" htmlFor="api-key-email">
            {t("apiKeys.email")}
          </label>
          <input
            id="api-key-email"
            type="email"
            className="input"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="api-key-name">
            {t("apiKeys.name")}{" "}
            <span className="font-normal text-gray-500">
              {t("apiKeys.optional")}
            </span>
          </label>
          <input
            id="api-key-name"
            type="text"
            className="input"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="api-key-days">
            {t("apiKeys.expiresInDays")}{" "}
            <span className="font-normal text-gray-500">
              {t("apiKeys.optional")}
            </span>
          </label>
          <input
            id="api-key-days"
            type="number"
            className="input"
            min={1}
            max={365}
            aria-describedby="api-key-days-hint"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
          <p id="api-key-days-hint" className="text-xs text-gray-500 mt-1">
            {t("apiKeys.expiresHint")}
          </p>
        </div>
        <div className="w-full">
          <label className="label" htmlFor="api-key-notes">
            {t("apiKeys.notes")}{" "}
            <span className="font-normal text-gray-500">
              {t("apiKeys.optional")}
            </span>
          </label>
          <textarea
            id="api-key-notes"
            className="input w-full"
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
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

      <p role="status" className="text-sm text-green-700">
        {sentTo && t("apiKeys.sent", { email: sentTo })}
      </p>
      {actionError && (
        <p role="alert" className="text-sm text-red-700">
          {t("apiKeys.actionFailed", { error: actionError })}
        </p>
      )}

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
                    "email",
                    "name",
                    "notes",
                    "status",
                    "expires",
                    "lastUsed",
                    "uses",
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
              {keys.map((k) => (
                <tr key={k.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 font-medium text-gray-900">
                    {k.email}
                  </td>
                  <td className="px-6 py-4">{k.name}</td>
                  <td className="px-6 py-4 text-gray-600 whitespace-pre-line">
                    {k.notes}
                  </td>
                  <td className="px-6 py-4">
                    <span className={`badge ${BADGE[k.status]}`}>
                      {t(`apiKeys.${k.status}`)}
                    </span>
                  </td>
                  <td className="px-6 py-4">{date(k.expires_at)}</td>
                  <td className="px-6 py-4">{date(k.last_used_at)}</td>
                  <td className="px-6 py-4">{k.use_count}</td>
                  <td className="px-6 py-4">
                    <div className="flex gap-2">
                      {(k.status === "unclaimed" || k.status === "active") && (
                        <button
                          type="button"
                          className="btn-danger btn-sm"
                          aria-label={t("apiKeys.revokeNamed", {
                            name: label(k),
                          })}
                          disabled={busyId !== null}
                          onClick={() => act(k, "revoke")}
                        >
                          {t("apiKeys.revoke")}
                        </button>
                      )}
                      {k.email && (
                        <button
                          type="button"
                          className="btn-secondary btn-sm"
                          aria-label={t("apiKeys.regenerateNamed", {
                            name: label(k),
                          })}
                          disabled={busyId !== null}
                          onClick={() => act(k, "regenerate")}
                        >
                          {t("apiKeys.regenerate")}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
