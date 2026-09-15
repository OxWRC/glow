// src/auth/AuthContext.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  AuthProvider,
  useAuth,
  useIsAdmin,
  useCurrentSchools,
} from "./AuthContext";
import type { MeResponse } from "../lib/api";

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

describe("AuthContext", () => {
  beforeEach(() => localStorage.clear());

  it("starts anonymous with no persisted state", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.identity).toBeNull();
    expect(result.current.token).toBeNull();
  });

  it("setIdentity persists token+identity to localStorage and updates derived hooks", () => {
    const identity: MeResponse = {
      kind: "authenticated",
      id: 1,
      username: "admin",
      is_admin: true,
      schools: [{ id: 5, name: "Test School" }],
    };
    const { result } = renderHook(
      () => ({
        auth: useAuth(),
        isAdmin: useIsAdmin(),
        schools: useCurrentSchools(),
      }),
      { wrapper },
    );
    act(() => result.current.auth.setIdentity(identity, "tok123"));
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.schools).toEqual([{ id: 5, name: "Test School" }]);
    expect(JSON.parse(localStorage.getItem("auth")!)).toMatchObject({
      token: "tok123",
      identity,
    });
  });

  it("logout clears identity/token and localStorage", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    act(() => result.current.setIdentity({ kind: "anonymous" }, "tok"));
    act(() => result.current.logout());
    expect(result.current.identity).toEqual({ kind: "anonymous" });
    expect(result.current.token).toBeNull();
    expect(localStorage.getItem("auth")).toBeNull();
  });

  it("recovers from malformed localStorage JSON instead of throwing", () => {
    localStorage.setItem("auth", "{not json");
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.identity).toBeNull();
    expect(localStorage.getItem("auth")).toBeNull();
  });
});
