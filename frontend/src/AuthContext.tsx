// Holds the current principal purely for UI convenience -- which nav items
// show, which buttons render. This is NOT the security boundary: every
// route still checks the role server-side (see harness/core/security.py).
// Removing this entire file would not let a CLIENT role do anything an
// OPERATOR could -- it would just make the UI stop hiding buttons that the
// backend would reject anyway.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getStoredApiKey, setStoredApiKey } from "./api";
import type { Principal } from "./types";

interface AuthState {
  principal: Principal | null;
  loading: boolean;
  error: string | null;
  login: (apiKey: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function tryRestore() {
    const key = getStoredApiKey();
    if (!key) {
      setLoading(false);
      return;
    }
    try {
      const me = await api.me();
      setPrincipal(me);
    } catch {
      setStoredApiKey(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    tryRestore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function login(apiKey: string) {
    setError(null);
    setStoredApiKey(apiKey);
    try {
      const me = await api.me();
      setPrincipal(me);
    } catch (e) {
      setStoredApiKey(null);
      setError(e instanceof Error ? e.message : "login failed");
      throw e;
    }
  }

  function logout() {
    setStoredApiKey(null);
    setPrincipal(null);
  }

  return (
    <AuthContext.Provider value={{ principal, loading, error, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
