<script lang="ts">
  import { goto } from '$app/navigation';
  import { authStore } from '$lib/stores';
  import { login, me, ApiError } from '$lib/api';
  import { createI18n, locale, type Locale } from '$lib/i18n';

  interface Props {
    data: { locale: Locale };
  }

  let { data }: Props = $props();

  const i18n = $derived(createI18n($locale));
  const currentLocale = $derived(data.locale || 'en');

  let username = $state('');
  let password = $state('');
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function handleSubmit(e: Event) {
    e.preventDefault();
    error = null;
    loading = true;
    try {
      const token = await login(username, password);
      const identity = await me(token.access_token);
      if (identity.kind !== "authenticated") {
        error = i18n.t('login.invalidCredentials');
        return;
      }
      authStore.setIdentity(identity, token.access_token);
      goto(`/${currentLocale}`);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 401) {
        error = i18n.t('login.invalidCredentials');
      } else {
        error = e instanceof Error ? e.message : i18n.t('login.loginFailed');
      }
    } finally {
      loading = false;
    }
  }
</script>

<svelte:head>
  <title>{i18n.t('login.signIn')} — {i18n.t('login.title')} {i18n.t('nav.dashboard')}</title>
</svelte:head>

<div class="min-h-screen bg-blue-900 flex items-center justify-center px-4">
  <div class="w-full max-w-md">
    <div class="text-center mb-8">
      <h1 class="text-3xl font-bold text-white">{i18n.t('login.title')}</h1>
      <p class="text-blue-200 mt-1">{i18n.t('login.subtitle')}</p>
    </div>

    <div class="bg-white rounded-2xl shadow-xl p-8">
      <h2 class="text-xl font-semibold text-gray-800 mb-6">{i18n.t('login.signIn')}</h2>

      <form onsubmit={handleSubmit} class="space-y-5">
        <div>
          <label class="label" for="username">{i18n.t('login.username')}</label>
          <input
            id="username"
            type="text"
            class="input"
            bind:value={username}
            autocomplete="username"
            required
            disabled={loading}
            placeholder={i18n.t('login.usernamePlaceholder')}
          />
        </div>

        <div>
          <label class="label" for="password">{i18n.t('login.password')}</label>
          <input
            id="password"
            type="password"
            class="input"
            bind:value={password}
            autocomplete="current-password"
            required
            disabled={loading}
            placeholder={i18n.t('login.passwordPlaceholder')}
          />
        </div>

        {#if error}
          <div class="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
            {error}
          </div>
        {/if}

        <button
          type="submit"
          class="btn-primary w-full justify-center py-2.5"
          disabled={loading}
        >
          {#if loading}
            <svg class="motion-safe:animate-spin -ml-1 mr-2 h-4 w-4" fill="none" viewBox="0 0 24 24" aria-hidden="true">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
            </svg>
            {i18n.t('login.signingIn')}
          {:else}
            {i18n.t('login.signIn')}
          {/if}
        </button>
      </form>
    </div>

    <p class="text-center text-blue-200 text-xs mt-6">
      {i18n.t('login.footer')}
    </p>
  </div>
</div>
