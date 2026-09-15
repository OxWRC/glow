/// <reference types="vite/client" />

export interface RuntimeConfig {
  apiBase: string;
  cognitoDomain: string;
  cognitoClientId: string;
  devAuthBypass: boolean;
}

declare global {
  interface Window {
    __ENV__?: Record<string, string>;
  }
}

export function getRuntimeConfig(): RuntimeConfig {
  const injected = window.__ENV__ ?? {};
  return {
    apiBase:
      injected.PUBLIC_API_BASE ??
      import.meta.env.VITE_PUBLIC_API_BASE ??
      "/api",
    cognitoDomain:
      injected.PUBLIC_COGNITO_DOMAIN ??
      import.meta.env.VITE_PUBLIC_COGNITO_DOMAIN ??
      "",
    cognitoClientId:
      injected.PUBLIC_COGNITO_CLIENT_ID ??
      import.meta.env.VITE_PUBLIC_COGNITO_CLIENT_ID ??
      "",
    devAuthBypass:
      (injected.PUBLIC_DEV_AUTH_BYPASS ??
        import.meta.env.VITE_PUBLIC_DEV_AUTH_BYPASS ??
        "false") === "true",
  };
}
