<script lang="ts">
  import { goto } from '$app/navigation';
  import { authStore, isAdmin } from '$lib/stores';
  import { listUsers, createUser, updateUser, deleteUser, listSchools } from '$lib/api';
  import type { User, UserCreate, UserUpdate, School } from '$lib/api';
  import { createI18n, locale, type Locale } from '$lib/i18n';

  interface Props {
    data: { locale: Locale };
  }

  let { data }: Props = $props();

  const i18n = $derived(createI18n($locale));
  const currentLocale = $derived(data.locale || 'en');

  // Redirect non-admins
  $effect(() => {
    if (!$isAdmin) goto(`/${currentLocale}`);
  });

  // ─── Load schools ─────────────────────────────────────────────────────────────

  let schoolsPromise = $derived.by(() => {
    if ($authStore.token) {
      return listSchools($authStore.token);
    }
    return Promise.resolve([]);
  });

  // ─── User list via {#await} ──────────────────────────────────────────────────

  let listVersion = $state(0); // increment to trigger a reload

  function loadUsers() {
    return listUsers($authStore.token!);
  }

  // Reactive promise: re-created whenever listVersion changes
  let usersPromise = $derived.by(() => { listVersion; return loadUsers(); });

  function reloadUsers() {
    listVersion += 1;
  }

  // ─── Modal state ─────────────────────────────────────────────────────────────

  type ModalMode = 'create' | 'edit' | null;
  let modalMode = $state<ModalMode>(null);
  let editingUser = $state<User | null>(null);
  let editDialog: HTMLDialogElement | null = $state(null);

  let formUsername = $state('');
  let formSchoolIds = $state<number[]>([]);
  let formIsActive = $state(true);
  let formIsAdmin = $state(false);
  let formLoading = $state(false);
  let formError = $state<string | null>(null);

  let deleteTarget = $state<User | null>(null);
  let deleteDialog: HTMLDialogElement | null = $state(null);
  let deleteLoading = $state(false);
  let actionError = $state<string | null>(null);

  function openCreate() {
    modalMode = 'create';
    editingUser = null;
    formUsername = '';
    formSchoolIds = [];
    formIsActive = true;
    formIsAdmin = false;
    formError = null;
    editDialog?.showModal();
  }

  function openEdit(user: User) {
    modalMode = 'edit';
    editingUser = user;
    formUsername = user.username;
    formSchoolIds = [...user.school_ids];
    formIsActive = user.is_active;
    formIsAdmin = user.is_admin;
    formError = null;
    editDialog?.showModal();
  }

  function closeModal() {
    modalMode = null;
    editingUser = null;
    formError = null;
    editDialog?.close();
  }

  $effect(() => {
    if (deleteTarget) {
      deleteDialog?.showModal();
    } else {
      deleteDialog?.close();
    }
  });

  async function handleSubmit() {
    formError = null;

    formLoading = true;
    try {
      if (modalMode === 'create') {
        const payload: UserCreate = {
          username: formUsername,
          school_ids: formSchoolIds,
          is_admin: formIsAdmin
        };
        await createUser($authStore.token!, payload);
      } else if (modalMode === 'edit' && editingUser) {
        const payload: UserUpdate = {
          school_ids: formSchoolIds,
          is_active: formIsActive,
          is_admin: formIsAdmin
        };
        await updateUser($authStore.token!, editingUser.id, payload);
      }
      closeModal();
      reloadUsers();
    } catch (e: unknown) {
      formError = e instanceof Error ? e.message : 'Action failed';
    } finally {
      formLoading = false;
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    deleteLoading = true;
    actionError = null;
    try {
      await deleteUser($authStore.token!, deleteTarget.id);
      deleteTarget = null;
      reloadUsers();
    } catch (e: unknown) {
      actionError = e instanceof Error ? e.message : 'Delete failed';
    } finally {
      deleteLoading = false;
    }
  }
</script>

<svelte:head>
  <title>{i18n.t('nav.admin')} — {i18n.t('login.title')} {i18n.t('nav.dashboard')}</title>
</svelte:head>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <div>
      <h1>{i18n.t('admin.title')}</h1>
      <p class="text-gray-500 mt-1">{i18n.t('admin.subtitle')}</p>
    </div>
    <button class="btn-primary" onclick={openCreate}>{i18n.t('admin.newUser')}</button>
  </div>

  {#if actionError}
    <div class="card bg-red-50 border-red-200 text-red-700 text-sm">{actionError}</div>
  {/if}

  {#await usersPromise}
    <div class="card flex items-center justify-center h-40 text-gray-400">
      <svg class="motion-safe:animate-spin h-8 w-8 mr-2" fill="none" viewBox="0 0 24 24" aria-hidden="true">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
      </svg>
      {i18n.t('admin.loadingUsers')}
    </div>
  {:then users}
    <div class="card p-0 overflow-hidden">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50">
          <tr>
            <th class="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">{i18n.t('admin.id')}</th>
            <th class="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">{i18n.t('admin.username')}</th>
            <th class="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">{i18n.t('admin.status')}</th>
            <th class="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">{i18n.t('admin.role')}</th>
            <th class="px-6 py-3 text-left font-semibold text-gray-600 uppercase tracking-wider text-xs">Schools</th>
            <th class="px-6 py-3 text-right font-semibold text-gray-600 uppercase tracking-wider text-xs">{i18n.t('admin.actions')}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 bg-white">
          {#each users as user}
            <tr class="hover:bg-gray-50">
              <td class="px-6 py-4 text-gray-400">{user.id}</td>
              <td class="px-6 py-4 font-medium text-gray-900">{user.username}</td>
              <td class="px-6 py-4">
                {#if user.is_active}
                  <span class="badge badge-green">{i18n.t('admin.active')}</span>
                {:else}
                  <span class="badge badge-red">{i18n.t('admin.inactive')}</span>
                {/if}
              </td>
              <td class="px-6 py-4">
                {#if user.is_admin}
                  <span class="badge badge-blue">{i18n.t('admin.admin')}</span>
                {:else}
                  <span class="badge badge-yellow">{i18n.t('admin.user')}</span>
                {/if}
              </td>
              <td class="px-6 py-4">
                {#if user.school_names.length > 0}
                  <span class="text-xs text-gray-600">{user.school_names.join(', ')}</span>
                {:else}
                  <span class="text-xs text-gray-400 italic">No schools</span>
                {/if}
              </td>
              <td class="px-6 py-4 text-right">
                <div class="flex items-center justify-end gap-2">
                  <button class="btn-secondary btn-sm" onclick={() => openEdit(user)}>{i18n.t('admin.edit')}</button>
                  {#if user.id !== $authStore.user?.id}
                    <button class="btn-danger btn-sm" onclick={() => (deleteTarget = user)}>{i18n.t('admin.delete')}</button>
                  {/if}
                </div>
              </td>
            </tr>
          {:else}
            <tr>
              <td colspan="6" class="px-6 py-8 text-center text-gray-400">{i18n.t('admin.noUsers')}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:catch error}
    <div class="card bg-red-50 border-red-200 text-red-700">
      Failed to load users: {error instanceof Error ? error.message : String(error)}
    </div>
  {/await}
</div>

<!-- Create/Edit Modal -->
<dialog bind:this={editDialog} class="rounded-2xl shadow-2xl w-full max-w-lg max-h-screen overflow-y-auto backdrop:bg-black/50 p-0">
  <div class="bg-white rounded-2xl">
    <div class="p-6 border-b border-gray-200">
      <h2 class="text-lg font-semibold">{modalMode === 'create' ? i18n.t('admin.createUser') : i18n.t('admin.editUser')}</h2>
    </div>
    <form method="dialog" class="p-6 space-y-4">
      <div>
        <label class="label" for="f-username">{i18n.t('admin.username')}</label>
        <input
          id="f-username"
          type="text"
          class="input"
          bind:value={formUsername}
          disabled={modalMode === 'edit'}
          placeholder="username"
        />
      </div>

      <div>
        <label class="label" for="f-schools">Schools</label>
        {#await schoolsPromise}
          <p class="text-sm text-gray-400">Loading schools...</p>
        {:then schools}
          <div class="space-y-2 max-h-48 overflow-y-auto border border-gray-200 rounded-md p-3">
            {#each schools as school}
              <label class="flex items-center gap-2 cursor-pointer">
                <input 
                  type="checkbox" 
                  class="rounded border-gray-300" 
                  value={school.id}
                  checked={formSchoolIds.includes(school.id)}
                  onchange={(e) => {
                    if (e.currentTarget.checked) {
                      formSchoolIds = [...formSchoolIds, school.id];
                    } else {
                      formSchoolIds = formSchoolIds.filter(id => id !== school.id);
                    }
                  }}
                />
                <span class="text-sm text-gray-700">{school.name}</span>
              </label>
            {:else}
              <p class="text-sm text-gray-400">No schools available</p>
            {/each}
          </div>
        {:catch error}
          <p class="text-sm text-red-600">Failed to load schools: {error.message}</p>
        {/await}
      </div>

      <div class="flex items-center gap-6">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="rounded border-gray-300" bind:checked={formIsActive} />
          <span class="text-sm text-gray-700">{i18n.t('admin.activeLabel')}</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="rounded border-gray-300" bind:checked={formIsAdmin} />
          <span class="text-sm text-gray-700">{i18n.t('admin.adminLabel')}</span>
        </label>
      </div>

      {#if formError}
        <div class="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {formError}
        </div>
      {/if}
    </form>
    <div class="p-6 border-t border-gray-200 flex justify-end gap-3">
      <button type="button" class="btn-secondary" onclick={closeModal} disabled={formLoading}>{i18n.t('admin.cancel')}</button>
      <button type="button" class="btn-primary" onclick={handleSubmit} disabled={formLoading}>
        {formLoading ? i18n.t('admin.saving') : modalMode === 'create' ? i18n.t('admin.create') : i18n.t('admin.save')}
      </button>
    </div>
  </div>
</dialog>

<!-- Delete confirmation -->
<dialog bind:this={deleteDialog} class="rounded-2xl shadow-2xl w-full max-w-sm backdrop:bg-black/50 p-0">
  <div class="bg-white rounded-2xl p-6 space-y-4">
    <h2 class="text-lg font-semibold text-gray-900">{i18n.t('admin.deleteUserTitle')}</h2>
    <p class="text-gray-600">
      {i18n.t('admin.deleteUserConfirm', { username: deleteTarget?.username || '' })}
    </p>
    <div class="flex justify-end gap-3">
      <button type="button" class="btn-secondary" onclick={() => (deleteTarget = null)} disabled={deleteLoading}>{i18n.t('admin.cancel')}</button>
      <button type="button" class="btn-danger" onclick={confirmDelete} disabled={deleteLoading}>
        {deleteLoading ? i18n.t('admin.deleting') : i18n.t('admin.delete')}
      </button>
    </div>
  </div>
</dialog>
