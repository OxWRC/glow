<script lang="ts">
  import '../../app.css';
  import { browser } from '$app/environment';
  import { authStore, isAdmin, isAuthenticated } from '$lib/stores';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { onMount, onDestroy } from 'svelte';
  import { checkHealth, me } from '$lib/api';
  import { createI18n, initializeLocale, locale, setLocale, type Locale } from '$lib/i18n';

  interface Props {
    children: import('svelte').Snippet;
    data: { locale: Locale; pathname: string };
  }

  let { children, data }: Props = $props();

  // Set locale from route params
  $effect(() => {
    if (data.locale) {
      setLocale(data.locale);
    }
  });

  const i18n = $derived(createI18n($locale));
  const currentLocale = $derived(data.locale || 'en');
  
  // Track path in state for use in authStore subscription
  let currentPath = $state('');
  
  $effect(() => {
    currentPath = $page.url.pathname;
  });

  // API health and version state
  type HealthStatus = 'unknown' | 'ok' | 'down';
  let apiHealth = $state<HealthStatus>('unknown');
  let apiVersion = $state<string | null>(null);
  let healthInterval: ReturnType<typeof setInterval> | null = null;

  async function pollHealth() {
    const health = await checkHealth();
    if (!health) {
      apiHealth = 'down';
      apiVersion = null;
      return;
    }

    apiVersion = health.version;
    apiHealth = 'ok';
  }

  // Bootstrap: fetch user identity from /me endpoint
  async function bootstrapIdentity() {
    try {
      const token = $authStore.token;
      const identity = await me(token || undefined);
      authStore.setIdentity(identity, token || undefined);
    } catch (error) {
      console.error('Failed to fetch identity:', error);
      // Fall back to anonymous
      authStore.setIdentity({ kind: "anonymous" });
    }
  }

  onMount(() => {
    initializeLocale();

    // Bootstrap identity on mount
    bootstrapIdentity();

    // Poll API health every 30 seconds
    pollHealth();
    healthInterval = setInterval(pollHealth, 30_000);

    return () => {
      if (healthInterval !== null) clearInterval(healthInterval);
    };
  });
  
  // No redirects - allow anonymous users to access dashboard

  function logout() {
    authStore.logout();
    goto(`/${currentLocale}/login`);
  }

  let mobileMenuOpen = $state(false);

  // Get display name from identity
  const displayName = $derived(() => {
    const identity = $authStore.identity;
    if (identity?.kind === "authenticated") {
      return identity.username;
    }
    return null;
  });
</script>

{#if $page.url.pathname.endsWith('/login')}
  {@render children()}
{:else}
  <div class="min-h-screen bg-gray-50">
    <!-- Navbar -->
    <nav class="bg-white border-b border-gray-200 shadow-sm">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex h-16 items-center justify-between">
          <div class="flex items-center gap-8">
            <a href="/{currentLocale}" class="flex items-center gap-2">
              <img src="/img/glow/glow-mark.png" alt="" class="h-8 w-8" />
              <span class="text-xl font-bold text-brand-700">GLOW</span>
              <span class="text-sm text-gray-400 hidden sm:block">{i18n.t('nav.dashboard')}</span>
            </a>
            <div class="hidden md:flex items-center gap-1">
              <a
                href="/{currentLocale}"
                class="px-3 py-2 rounded-md text-sm font-medium transition-colors {$page.url.pathname === `/${currentLocale}` ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'}"
              >
                {i18n.t('nav.home')}
              </a>
              {#if $isAdmin}
                <a
                  href="/{currentLocale}/admin"
                  class="px-3 py-2 rounded-md text-sm font-medium transition-colors {$page.url.pathname.startsWith(`/${currentLocale}/admin`) ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'}"
                >
                  {i18n.t('nav.admin')}
                </a>
              {/if}
            </div>
          </div>

          <div class="flex items-center gap-3">
            <!-- API health indicator -->
            <span
              class="hidden sm:flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full
                {apiHealth === 'ok' ? 'bg-green-50 text-green-700' :
                 apiHealth === 'down' ? 'bg-red-50 text-red-700' :
                 'bg-gray-50 text-gray-400'}"
              title={apiHealth === 'ok' ? `API v${apiVersion}` :
                     apiHealth === 'down' ? 'API is unreachable' :
                     'Checking API status...'}
            >
              {#if apiHealth === 'ok'}
                <span class="h-1.5 w-1.5 rounded-full bg-green-500 inline-block"></span>
                {i18n.t('nav.api')}
              {:else if apiHealth === 'down'}
                <!-- Unplugged icon -->
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M18.364 5.636a9 9 0 010 12.728M15.536 8.464a5 5 0 010 7.072M9 9l6 6M3 3l18 18" />
                </svg>
                {i18n.t('nav.apiDown')}
              {:else}
                <span class="h-1.5 w-1.5 rounded-full bg-gray-300 inline-block motion-safe:animate-pulse"></span>
                {i18n.t('nav.api')}
              {/if}
            </span>

            {#if displayName()}
              <div class="hidden md:flex items-center gap-2">
                <span class="text-sm text-gray-500">
                  {displayName()}
                </span>
                {#if $isAdmin}
                  <span class="badge badge-blue">{i18n.t('nav.adminBadge')}</span>
                {/if}
              </div>
            {/if}
            {#if $isAuthenticated}
              <button class="btn-secondary btn-sm" onclick={logout}>{i18n.t('nav.signOut')}</button>
            {:else}
              <a href="/{currentLocale}/login" class="btn-primary btn-sm">{i18n.t('nav.signIn')}</a>
            {/if}
            <button
              class="md:hidden p-2 rounded-md text-gray-500 hover:bg-gray-100"
              onclick={() => (mobileMenuOpen = !mobileMenuOpen)}
              aria-label="Menu"
            >
              <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      <!-- Mobile menu -->
      {#if mobileMenuOpen}
        <div class="md:hidden border-t border-gray-200 px-4 py-3 space-y-1">
          <a href="/{currentLocale}" class="block px-3 py-2 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-100" onclick={() => (mobileMenuOpen = false)}>{i18n.t('nav.home')}</a>
          {#if $isAdmin}
            <a href="/{currentLocale}/admin" class="block px-3 py-2 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-100" onclick={() => (mobileMenuOpen = false)}>{i18n.t('nav.admin')}</a>
          {/if}
          {#if displayName()}
            <div class="px-3 py-2 text-sm text-gray-500">{displayName()}</div>
          {/if}
          <!-- API health in mobile menu -->
          <div class="px-3 py-2 text-xs text-gray-400">
            {i18n.t('nav.api')}: 
            {#if apiHealth === 'ok'}
              {i18n.t('nav.online')} (v{apiVersion})
            {:else if apiHealth === 'down'}
              {i18n.t('nav.offline')}
            {:else}
              {i18n.t('nav.checking')}
            {/if}
          </div>
        </div>
      {/if}
    </nav>

    <!-- Main content -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {@render children()}
    </main>
  </div>
{/if}
