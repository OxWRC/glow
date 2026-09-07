import { writable, derived } from "svelte/store";
import type { User, MeResponse, SchoolSummary } from "./api";

interface AuthState {
  token: string | null;
  user: User | null; // Legacy - kept for backward compatibility
  identity: MeResponse | null; // New identity from /me endpoint
}

function createAuthStore() {
  const stored =
    typeof localStorage !== "undefined" ? localStorage.getItem("auth") : null;
  let initial: AuthState;
  try {
    initial = stored
      ? (JSON.parse(stored) as AuthState)
      : {
          token: null,
          user: null,
          identity: null,
        };
  } catch {
    // If localStorage contains invalid JSON, clear it and start fresh
    initial = { token: null, user: null, identity: null };
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem("auth");
    }
  }

  const { subscribe, set, update } = writable<AuthState>(initial);

  return {
    subscribe,
    login(token: string, user: User) {
      const state = { token, user, identity: null };
      set(state);
      localStorage.setItem("auth", JSON.stringify(state));
    },
    setIdentity(identity: MeResponse, token?: string) {
      update((state) => {
        const newState = {
          ...state,
          identity,
          token: identity.kind === "authenticated" ? (token ?? state.token) : null,
        };
        localStorage.setItem("auth", JSON.stringify(newState));
        return newState;
      });
    },
    logout() {
      set({ token: null, user: null, identity: { kind: "anonymous" } });
      localStorage.removeItem("auth");
    },
    update,
  };
}

export const authStore = createAuthStore();

export const isAuthenticated = derived(
  authStore,
  ($auth) => $auth.identity?.kind === "authenticated",
);

export const isAdmin = derived(
  authStore,
  ($auth) =>
    $auth.identity?.kind === "authenticated" && $auth.identity.is_admin,
);

export const currentSchools = derived(authStore, ($auth): SchoolSummary[] => {
  if ($auth.identity?.kind === "authenticated") {
    return $auth.identity.schools;
  }
  return [];
});

export const columnsStore = writable<string[]>([]);
