/// <reference types="vite/client" />

export interface RuntimeConfig {
  apiBase: string;
  oidcIssuer: string;
  oidcClientId: string;
  demoMode: boolean;
}

declare global {
  interface Window {
    __ENV__?: Record<string, string>;
  }
}

let cached: RuntimeConfig | null = null;

export async function loadRuntimeConfig(): Promise<RuntimeConfig> {
  if (cached) return cached;
  try {
    const res = await fetch("/config.json");
    if (res.ok) {
      const injected = (await res.json()) as Record<string, string>;
      window.__ENV__ = injected;
    }
  } catch {
    // Vite's dev server has no /config.json, but it serves index.html (200
    // OK) for unknown paths rather than failing the fetch, so this catch is
    // actually reached via a JSON-parse error on that HTML body, not a
    // network-level rejection. Either way, getRuntimeConfig() falls back to
    // import.meta.env.VITE_* per Task 2.
  }
  cached = getRuntimeConfig();
  return cached;
}

export function getRuntimeConfig(): RuntimeConfig {
  const injected = window.__ENV__ ?? {};
  return {
    apiBase:
      injected.PUBLIC_API_BASE ??
      import.meta.env.VITE_PUBLIC_API_BASE ??
      "/api",
    oidcIssuer:
      injected.PUBLIC_OIDC_ISSUER ??
      import.meta.env.VITE_PUBLIC_OIDC_ISSUER ??
      "",
    oidcClientId:
      injected.PUBLIC_OIDC_CLIENT_ID ??
      import.meta.env.VITE_PUBLIC_OIDC_CLIENT_ID ??
      "",
    demoMode:
      (injected.PUBLIC_DEMO_MODE ??
        import.meta.env.VITE_PUBLIC_DEMO_MODE ??
        "false") === "true",
  };
}
