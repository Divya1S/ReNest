import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { apiFetch, fetchCsrfCookie, setApiAuthHandlers } from "../lib/api";
import { clearApiCache } from "../lib/apiCache";
import { dropBrowserPushSubscription, removePushSubscription } from "../lib/push";
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

// Upper bound on the push clean-up that runs before logout. Signing out must
// never wait on a slow push service.
const PUSH_CLEANUP_BUDGET_MS = 6000;

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
      void dropBrowserPushSubscription();
    }
    return data.user;
  }, []);

  useEffect(() => {
    setApiAuthHandlers({
      onSessionExpired({ message }) {
        if (!userRef.current) return; // anonymous visitor, nothing expired
        clearApiCache();
        setUser(null);
        setAuthNotice(message);
        // No session left to call /push/unsubscribe with, so at least stop
        // this browser from receiving the expired account's notifications.
        void dropBrowserPushSubscription();
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
        // Shared with apiFetch: a login or register submitted while this is
        // still in flight waits for the same response, so it can never be
        // sent without the CSRF header.
        await fetchCsrfCookie();
        const data = await apiFetch<AuthMeResponse>("/auth/me");
        if (active) {
          // No clearApiCache here: on first load the cache can only hold public
          // data this same page just fetched, and dropping it strands the view.
          setUser(data.user);
        }
        // The server says nobody is signed in. A push subscription still held
        // by this browser belongs to an earlier session that ended without a
        // logout (expired, cookies cleared): drop it before someone else signs
        // in here. Only on a definite answer, never on a failed request.
        if (!data.user) void dropBrowserPushSubscription();
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
    // Before the session ends, because /push/unsubscribe needs it. A push
    // subscription left bound to this account would keep delivering its
    // notifications to whoever uses this browser next.
    let budgetTimer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      removePushSubscription().catch(() => false),
      new Promise((resolve) => {
        budgetTimer = setTimeout(resolve, PUSH_CLEANUP_BUDGET_MS);
      }),
    ]);
    clearTimeout(budgetTimer);
    await apiFetch("/auth/logout", { method: "POST", body: {} });
    clearApiCache();
    setAuthNotice("");
    setUser(null);
  }, []);

  // One stable object per auth state. A fresh literal on every render made
  // every useAuth() consumer re-render whenever the provider did.
  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, authNotice, clearAuthNotice, refreshUser, login, register, logout }),
    [user, loading, authNotice, clearAuthNotice, refreshUser, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext) ?? defaultAuthContext;
}
