import { createContext, useContext, useState, useCallback, ReactNode } from "react";
import type { MeResponse, SchoolSummary } from "../lib/api";

interface AuthState {
  token: string | null;
  identity: MeResponse | null;
}

interface AuthContextValue extends AuthState {
  setIdentity(identity: MeResponse, token?: string): void;
  logout(): void;
}

const STORAGE_KEY = "auth";

function loadInitialState(): AuthState {
  const stored =
    typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
  if (!stored) return { token: null, identity: null };
  try {
    const parsed = JSON.parse(stored) as AuthState;
    return { token: parsed.token ?? null, identity: parsed.identity ?? null };
  } catch {
    if (typeof localStorage !== "undefined") localStorage.removeItem(STORAGE_KEY);
    return { token: null, identity: null };
  }
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(loadInitialState);

  const setIdentity = useCallback((identity: MeResponse, token?: string) => {
    setState((prev) => {
      const next: AuthState = {
        identity,
        token: identity.kind === "authenticated" ? (token ?? prev.token) : null,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const logout = useCallback(() => {
    setState({ token: null, identity: { kind: "anonymous" } });
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, setIdentity, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function useIsAuthenticated(): boolean {
  return useAuth().identity?.kind === "authenticated";
}

export function useIsAdmin(): boolean {
  const { identity } = useAuth();
  return identity?.kind === "authenticated" && identity.is_admin;
}

export function useCurrentSchools(): SchoolSummary[] {
  const { identity } = useAuth();
  return identity?.kind === "authenticated" ? identity.schools : [];
}
