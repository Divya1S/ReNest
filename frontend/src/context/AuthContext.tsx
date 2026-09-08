import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { apiFetch, setApiAuthHandlers } from "../lib/api";
import { clearApiCache } from "../lib/apiCache";
import type { AuthActionResponse, AuthMeResponse, User } from "../types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  authNotice: string;
  clearAuthNotice: () => void;
  refreshUser: () => Promise<User | null>;
  login: (form: Record<string, unknown>) => Promise<User>;
  register: (form: Record<string, unknown>) => Promise<AuthActionResponse>;
  logout: () => Promise<void>;
}

const defaultAuthContext: AuthContextValue = {
  user: null,
  loading: false,
  authNotice: "",
  clearAuthNotice: () => undefined,
  refreshUser: async () => null,
  login: async () => { throw new Error("AuthProvider not mounted"); },
  register: async () => { throw new Error("AuthProvider not mounted"); },
  logout: async () => undefined,
};

export const AuthContext = createContext<AuthContextValue>(defaultAuthContext);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [authNotice, setAuthNotice] = useState("");

  // Tracks whether a signed-in session existed, so a guest hitting a 401 on a
  // public page is never shown a spurious "session expired" notice.
  const userRef = useRef<User | null>(null);
  useEffect(() => {
    userRef.current = user;
  }, [user]);

  const clearAuthNotice = useCallback(() => setAuthNotice(""), []);

  const refreshUser = useCallback(async (): Promise<User | null> => {
    const data = await apiFetch<AuthMeResponse>("/auth/me");
    const hadUser = userRef.current;
    setUser(data.user);
    // Only clear on a signed-in -> signed-out transition. Clearing for a
    // visitor who was never signed in wipes public data the page just fetched
    // (a listing detail loaded while auth was still bootstrapping), and
    // nothing refetches it, and the page then reads "Listing not found".
    if (!data.user && hadUser) {
      clearApiCache();
      setAuthNotice("");
    }
    return data.user;
  }, []);

  useEffect(() => {
    setApiAuthHandlers({
      onSessionExpired({ message }) {
        if (!userRef.current) return; // anonymous visitor — nothing expired
        clearApiCache();
        setUser(null);
        setAuthNotice(message);
      },
    });
    return () => setApiAuthHandlers({ onSessionExpired: null });
  }, []);

  // While the signed-in user is unverified, re-check on tab focus: they
  // typically click the verification link in an email client or another tab,
  // and the original tab must notice without a manual reload.
  useEffect(() => {
    function onFocus() {
      const current = userRef.current;
      if (current && !current.email_verified) {
        refreshUser().catch(() => undefined);
      }
    }
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refreshUser]);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      try {
        await fetch("/api/auth/csrf", { credentials: "include" });
        const data = await apiFetch<AuthMeResponse>("/auth/me");
        if (active) {
          // No clearApiCache here: on first load the cache can only hold public
          // data this same page just fetched, and dropping it strands the view.
          setUser(data.user);
        }
      } catch {
        if (active) setUser(null);
      } finally {
        if (active) setLoading(false);
      }
    }

    bootstrap();
    return () => { active = false; };
  }, []);

  const login = useCallback(async (form: Record<string, unknown>): Promise<User> => {
    const data = await apiFetch<AuthActionResponse>("/auth/login", { method: "POST", body: form });
    clearApiCache();
    setAuthNotice("");
    setUser(data.user);
    return data.user;
  }, []);

  const register = useCallback(async (form: Record<string, unknown>): Promise<AuthActionResponse> => {
    const data = await apiFetch<AuthActionResponse>("/auth/register", { method: "POST", body: form });
    clearApiCache();
    setAuthNotice("");
    setUser(data.user);
    return data;
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    await apiFetch("/auth/logout", { method: "POST", body: {} });
    clearApiCache();
    setAuthNotice("");
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{ user, loading, authNotice, clearAuthNotice, refreshUser, login, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext) ?? defaultAuthContext;
}
