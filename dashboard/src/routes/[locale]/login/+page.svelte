<script lang="ts">
  import { goto } from '$app/navigation';
  import { authStore } from '$lib/stores';
  import { me, ApiError, devLogin, COGNITO_DOMAIN, COGNITO_CLIENT_ID, DEV_AUTH_BYPASS } from '$lib/api';
  import { createI18n, locale, type Locale } from '$lib/i18n';
  import { onMount } from 'svelte';

  interface Props {
    data: { locale: Locale };
  }

  let { data }: Props = $props();

  const i18n = $derived(createI18n($locale));
  const currentLocale = $derived(data.locale || 'en');

  let loading = $state(false);
  let error = $state<string | null>(null);
  let schoolIdInput = $state('');

  function base64url(bytes: Uint8Array): string {
    let str = '';
    for (const b of bytes) str += String.fromCharCode(b);
    return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  // Shared by the PKCE verifier and the CSRF `state` value - both are just
  // "a random, unguessable token", no need for two generators.
  function randomToken(): string {
    return base64url(crypto.getRandomValues(new Uint8Array(32)));
  }

  async function generateCodeChallenge(verifier: string): Promise<string> {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
    return base64url(new Uint8Array(digest));
  }

  async function redirectToCognito() {
    if (!COGNITO_DOMAIN || !COGNITO_CLIENT_ID) {
      error = i18n.t('login.configError');
      return;
    }
    const verifier = randomToken();
    sessionStorage.setItem('pkce_code_verifier', verifier);
    const state = randomToken();
    sessionStorage.setItem('oauth_state', state);
    const challenge = await generateCodeChallenge(verifier);
    const redirectUri = `${window.location.origin}/${currentLocale}/auth/callback`;
    const params = new URLSearchParams({
      client_id: COGNITO_CLIENT_ID,
      response_type: 'code',
      scope: 'openid email profile',
      redirect_uri: redirectUri,
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
    });
    window.location.href = `https://${COGNITO_DOMAIN}/oauth2/authorize?${params.toString()}`;
  }

  onMount(() => {
    if (!DEV_AUTH_BYPASS) {
      redirectToCognito().catch((e: unknown) => {
        error = e instanceof Error ? e.message : i18n.t('login.loginFailed');
      });
    }
  });

  async function loginAs(role: 'admin' | 'wrc' | 'school') {
    error = null;
    loading = true;
    try {
      const trimmed = schoolIdInput.trim();
      const schoolId = role === 'school' && trimmed ? Number(trimmed) : undefined;
      const token = await devLogin(role, schoolId);
      const identity = await me(token.access_token);
      if (identity.kind !== 'authenticated') {
        error = i18n.t('login.loginFailed');
        return;
      }
      authStore.setIdentity(identity, token.access_token);
      goto(`/${currentLocale}`);
    } catch (e: unknown) {
      error =
        e instanceof ApiError ? e.message : e instanceof Error ? e.message : i18n.t('login.loginFailed');
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
      {#if DEV_AUTH_BYPASS}
        <h2 class="text-xl font-semibold text-gray-800 mb-6">{i18n.t('login.devPickerTitle')}</h2>

        <div class="space-y-4">
          <div>
            <label class="label" for="school-id">{i18n.t('login.schoolIdLabel')}</label>
            <input
              id="school-id"
              type="text"
              inputmode="numeric"
              pattern="[0-9]*"
              class="input"
              bind:value={schoolIdInput}
              disabled={loading}
              placeholder={i18n.t('login.schoolIdPlaceholder')}
            />
          </div>

          {#if error}
            <div class="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
              {error}
            </div>
          {/if}

          <div class="grid grid-cols-3 gap-2">
            <button
              type="button"
              class="btn-primary justify-center py-2.5"
              disabled={loading}
              onclick={() => loginAs('admin')}
            >
              {i18n.t('login.devAdmin')}
            </button>
            <button
              type="button"
              class="btn-primary justify-center py-2.5"
              disabled={loading}
              onclick={() => loginAs('wrc')}
            >
              {i18n.t('login.devWrc')}
            </button>
            <button
              type="button"
              class="btn-primary justify-center py-2.5"
              disabled={loading}
              onclick={() => loginAs('school')}
            >
              {i18n.t('login.devSchool')}
            </button>
          </div>
        </div>
      {:else if error}
        <div class="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {error}
        </div>
      {:else}
        <div class="flex items-center justify-center gap-3 py-4">
          <svg class="motion-safe:animate-spin h-5 w-5 text-blue-700" fill="none" viewBox="0 0 24 24" aria-hidden="true">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          <span class="text-gray-600">{i18n.t('login.redirecting')}</span>
        </div>
      {/if}
    </div>

    <p class="text-center text-blue-200 text-xs mt-6">
      {i18n.t('login.footer')}
    </p>
  </div>
</div>
