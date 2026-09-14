<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { authStore } from '$lib/stores';
  import { me, exchangeCodeForToken } from '$lib/api';
  import { createI18n, locale, type Locale } from '$lib/i18n';

  interface Props {
    data: { locale: Locale };
  }

  let { data }: Props = $props();

  const i18n = $derived(createI18n($locale));
  const currentLocale = $derived(data.locale || 'en');

  let error = $state<string | null>(null);

  onMount(async () => {
    const params = $page.url.searchParams;

    // Verify state before anything else, including the error branch below -
    // Cognito echoes state back on both success and error redirects.
    const returnedState = params.get('state');
    const expectedState = sessionStorage.getItem('oauth_state');
    sessionStorage.removeItem('oauth_state');
    if (!expectedState || returnedState !== expectedState) {
      error = i18n.t('login.loginFailed');
      return;
    }

    const oauthError = params.get('error');
    if (oauthError) {
      error = params.get('error_description') ?? oauthError;
      return;
    }

    const code = params.get('code');
    const verifier = sessionStorage.getItem('pkce_code_verifier');
    sessionStorage.removeItem('pkce_code_verifier');

    if (!code || !verifier) {
      error = i18n.t('login.loginFailed');
      return;
    }

    try {
      const redirectUri = `${window.location.origin}/${currentLocale}/auth/callback`;
      const tokens = await exchangeCodeForToken(code, redirectUri, verifier);
      const identity = await me(tokens.id_token);
      if (identity.kind !== 'authenticated') {
        error = i18n.t('login.loginFailed');
        return;
      }
      authStore.setIdentity(identity, tokens.id_token);
      goto(`/${currentLocale}`);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : i18n.t('login.loginFailed');
    }
  });
</script>

<svelte:head>
  <title>{i18n.t('login.signingIn')} — {i18n.t('login.title')}</title>
</svelte:head>

<div class="min-h-screen bg-blue-900 flex items-center justify-center px-4">
  <div class="w-full max-w-md">
    <div class="bg-white rounded-2xl shadow-xl p-8 text-center">
      {#if error}
        <div class="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700 mb-4">
          {error}
        </div>
        <a href="/{currentLocale}/login" class="btn-primary inline-block py-2.5 px-6">
          {i18n.t('login.backToLogin')}
        </a>
      {:else}
        <div class="flex items-center justify-center gap-3 py-4">
          <svg class="motion-safe:animate-spin h-5 w-5 text-blue-700" fill="none" viewBox="0 0 24 24" aria-hidden="true">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          <span class="text-gray-600">{i18n.t('login.signingIn')}</span>
        </div>
      {/if}
    </div>
  </div>
</div>
