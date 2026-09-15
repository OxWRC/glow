import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  listSchools,
  type User,
  type UserCreate,
  type UserUpdate,
  type School,
} from "../lib/api";
import { useAuth, useIsAdmin } from "../auth/AuthContext";
import { createI18n, availableLocales, type Locale } from "../lib/i18n";

type ModalMode = "create" | "edit" | null;

export function AdminPage() {
  const { locale: localeParam } = useParams<{ locale: string }>();
  const locale: Locale = availableLocales.includes(localeParam as Locale)
    ? (localeParam as Locale)
    : "en";
  const i18n = useMemo(() => createI18n(locale), [locale]);

  const { token, identity } = useAuth();
  const isAdmin = useIsAdmin();
  const navigate = useNavigate();

  // Self-delete guard fix: the old app compared against `$authStore.user?.id`,
  // which nothing in the real login flow ever populates (dead code - the
  // delete button always showed, even for your own row). Compare against the
  // real authenticated identity instead.
  const currentUserId =
    identity?.kind === "authenticated" ? identity.id : undefined;

  // Redirect non-admins
  useEffect(() => {
    if (!isAdmin) navigate(`/${locale}`);
  }, [isAdmin, locale, navigate]);

  // ─── Schools ────────────────────────────────────────────────────────────
  const [schools, setSchools] = useState<School[] | null>(null);
  const [schoolsError, setSchoolsError] = useState<string | null>(null);
  useEffect(() => {
    if (!token) return;
    listSchools(token)
      .then((s) => {
        setSchools(s);
        setSchoolsError(null);
      })
      .catch((e: unknown) => {
        setSchoolsError(e instanceof Error ? e.message : String(e));
      });
  }, [token]);

  // ─── Users ──────────────────────────────────────────────────────────────
  const [users, setUsers] = useState<User[] | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);

  const reloadUsers = useCallback(async () => {
    if (!token) return;
    try {
      const loaded = await listUsers(token);
      setUsers(loaded);
      setUsersError(null);
    } catch (e: unknown) {
      setUsers(null);
      setUsersError(e instanceof Error ? e.message : String(e));
    }
  }, [token]);

  useEffect(() => {
    reloadUsers();
  }, [reloadUsers]);

  // ─── Modal state ────────────────────────────────────────────────────────
  const [modalMode, setModalMode] = useState<ModalMode>(null);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const editDialogRef = useRef<HTMLDialogElement>(null);

  const [formUsername, setFormUsername] = useState("");
  const [formSchoolIds, setFormSchoolIds] = useState<number[]>([]);
  const [formIsActive, setFormIsActive] = useState(true);
  const [formIsAdmin, setFormIsAdmin] = useState(false);
  const [formLoading, setFormLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const deleteDialogRef = useRef<HTMLDialogElement>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  function openCreate() {
    setModalMode("create");
    setEditingUser(null);
    setFormUsername("");
    setFormSchoolIds([]);
    setFormIsActive(true);
    setFormIsAdmin(false);
    setFormError(null);
    editDialogRef.current?.showModal();
  }

  function openEdit(user: User) {
    setModalMode("edit");
    setEditingUser(user);
    setFormUsername(user.username);
    setFormSchoolIds([...user.school_ids]);
    setFormIsActive(user.is_active);
    setFormIsAdmin(user.is_admin);
    setFormError(null);
    editDialogRef.current?.showModal();
  }

  function closeModal() {
    setModalMode(null);
    setEditingUser(null);
    setFormError(null);
    editDialogRef.current?.close();
  }

  useEffect(() => {
    if (deleteTarget) {
      deleteDialogRef.current?.showModal();
    } else {
      deleteDialogRef.current?.close();
    }
  }, [deleteTarget]);

  async function handleSubmit() {
    if (!token) return;
    setFormError(null);
    setFormLoading(true);
    try {
      if (modalMode === "create") {
        const payload: UserCreate = {
          username: formUsername,
          school_ids: formSchoolIds,
          is_admin: formIsAdmin,
        };
        await createUser(token, payload);
      } else if (modalMode === "edit" && editingUser) {
        const payload: UserUpdate = {
          school_ids: formSchoolIds,
          is_active: formIsActive,
          is_admin: formIsAdmin,
        };
        await updateUser(token, editingUser.id, payload);
      }
      closeModal();
      await reloadUsers();
    } catch (e: unknown) {
      setFormError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setFormLoading(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || !token) return;
    setDeleteLoading(true);
    setActionError(null);
    try {
      await deleteUser(token, deleteTarget.id);
      setDeleteTarget(null);
      await reloadUsers();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleteLoading(false);
    }
  }

  useEffect(() => {
    document.title = `${i18n.t("nav.admin")} — ${i18n.t("login.title")} ${i18n.t("nav.dashboard")}`;
  }, [i18n]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1>{i18n.t("admin.title")}</h1>
          <p className="text-gray-500 mt-1">{i18n.t("admin.subtitle")}</p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          {i18n.t("admin.newUser")}
        </button>
      </div>

      {actionError && (
        <div className="card bg-red-50 border-red-200 text-red-700 text-sm">
          {actionError}
        </div>
      )}

      {users === null ? (
        usersError ? (
          <div className="card bg-red-50 border-red-200 text-red-700">
            Failed to load users: {usersError}
          </div>
        ) : (
          <div className="card flex items-center justify-center h-40 text-gray-400">
            <svg
              className="motion-safe:animate-spin h-8 w-8 mr-2"
              fill="none"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              ></circle>
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"
              ></path>
            </svg>
            {i18n.t("admin.loadingUsers")}
          </div>
        )
      ) : (
        <div className="card p-0 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">
                  {i18n.t("admin.id")}
                </th>
                <th className="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">
                  {i18n.t("admin.username")}
                </th>
                <th className="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">
                  {i18n.t("admin.status")}
                </th>
                <th className="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">
                  {i18n.t("admin.role")}
                </th>
                <th className="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">
                  Schools
                </th>
                <th className="px-6 py-3 text-right font-semibold text-gray-600 uppercase tracking-wider text-xs">
                  {i18n.t("admin.actions")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {users.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-6 py-8 text-center text-gray-400"
                  >
                    {i18n.t("admin.noUsers")}
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr key={user.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 text-gray-400">{user.id}</td>
                    <td className="px-6 py-4 font-medium text-gray-900">
                      {user.username}
                    </td>
                    <td className="px-6 py-4">
                      {user.is_active ? (
                        <span className="badge badge-green">
                          {i18n.t("admin.active")}
                        </span>
                      ) : (
                        <span className="badge badge-red">
                          {i18n.t("admin.inactive")}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4">
                      {user.is_admin ? (
                        <span className="badge badge-blue">
                          {i18n.t("admin.admin")}
                        </span>
                      ) : (
                        <span className="badge badge-yellow">
                          {i18n.t("admin.user")}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4">
                      {user.school_names.length > 0 ? (
                        <span className="text-xs text-gray-600">
                          {user.school_names.join(", ")}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400 italic">
                          No schools
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          className="btn-secondary btn-sm"
                          onClick={() => openEdit(user)}
                        >
                          {i18n.t("admin.edit")}
                        </button>
                        {user.id !== currentUserId && (
                          <button
                            className="btn-danger btn-sm"
                            onClick={() => setDeleteTarget(user)}
                          >
                            {i18n.t("admin.delete")}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      <dialog
        ref={editDialogRef}
        className="rounded-2xl shadow-2xl w-full max-w-lg max-h-screen overflow-y-auto backdrop:bg-black/50 p-0"
      >
        <div className="bg-white rounded-2xl">
          <div className="p-6 border-b border-gray-200">
            <h2 className="text-lg font-semibold">
              {modalMode === "create"
                ? i18n.t("admin.createUser")
                : i18n.t("admin.editUser")}
            </h2>
          </div>
          <form method="dialog" className="p-6 space-y-4">
            <div>
              <label className="label" htmlFor="f-username">
                {i18n.t("admin.username")}
              </label>
              <input
                id="f-username"
                type="text"
                className="input"
                value={formUsername}
                onChange={(e) => setFormUsername(e.target.value)}
                disabled={modalMode === "edit"}
                placeholder="username"
              />
            </div>

            <div>
              <label className="label" htmlFor="f-schools">
                Schools
              </label>
              {schoolsError ? (
                <p className="text-sm text-red-600">
                  Failed to load schools: {schoolsError}
                </p>
              ) : schools === null ? (
                <p className="text-sm text-gray-400">Loading schools...</p>
              ) : schools.length > 0 ? (
                <div className="space-y-2 max-h-48 overflow-y-auto border border-gray-200 rounded-md p-3">
                  {schools.map((school) => (
                    <label
                      key={school.id}
                      className="flex items-center gap-2 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        className="rounded border-gray-300"
                        checked={formSchoolIds.includes(school.id)}
                        onChange={(e) => {
                          if (e.currentTarget.checked) {
                            setFormSchoolIds((prev) => [...prev, school.id]);
                          } else {
                            setFormSchoolIds((prev) =>
                              prev.filter((id) => id !== school.id),
                            );
                          }
                        }}
                      />
                      <span className="text-sm text-gray-700">
                        {school.name}
                      </span>
                    </label>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-400">No schools available</p>
              )}
            </div>

            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="rounded border-gray-300"
                  checked={formIsActive}
                  onChange={(e) => setFormIsActive(e.target.checked)}
                />
                <span className="text-sm text-gray-700">
                  {i18n.t("admin.activeLabel")}
                </span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="rounded border-gray-300"
                  checked={formIsAdmin}
                  onChange={(e) => setFormIsAdmin(e.target.checked)}
                />
                <span className="text-sm text-gray-700">
                  {i18n.t("admin.adminLabel")}
                </span>
              </label>
            </div>

            {formError && (
              <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
                {formError}
              </div>
            )}
          </form>
          <div className="p-6 border-t border-gray-200 flex justify-end gap-3">
            <button
              type="button"
              className="btn-secondary"
              onClick={closeModal}
              disabled={formLoading}
            >
              {i18n.t("admin.cancel")}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={handleSubmit}
              disabled={formLoading}
            >
              {formLoading
                ? i18n.t("admin.saving")
                : modalMode === "create"
                  ? i18n.t("admin.create")
                  : i18n.t("admin.save")}
            </button>
          </div>
        </div>
      </dialog>

      {/* Delete confirmation */}
      <dialog
        ref={deleteDialogRef}
        className="rounded-2xl shadow-2xl w-full max-w-sm backdrop:bg-black/50 p-0"
      >
        <div className="bg-white rounded-2xl p-6 space-y-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {i18n.t("admin.deleteUserTitle")}
          </h2>
          <p className="text-gray-600">
            {i18n.t("admin.deleteUserConfirm", {
              username: deleteTarget?.username || "",
            })}
          </p>
          <div className="flex justify-end gap-3">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setDeleteTarget(null)}
              disabled={deleteLoading}
            >
              {i18n.t("admin.cancel")}
            </button>
            <button
              type="button"
              className="btn-danger"
              onClick={confirmDelete}
              disabled={deleteLoading}
            >
              {deleteLoading
                ? i18n.t("admin.deleting")
                : i18n.t("admin.delete")}
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
