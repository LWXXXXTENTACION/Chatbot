"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import type { UserInfo } from "@/lib/api";
import { api } from "@/lib/api";
import { clearTokens, getAccessToken, setTokens, tryRefreshToken } from "@/lib/auth";
import { useChatStore } from "@/lib/store";

interface AuthContextValue {
  user: UserInfo | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check token validity on mount
  useEffect(() => {
    let cancelled = false;

    async function init() {
      const token = getAccessToken();
      if (!token) {
        // Try refresh
        const refreshed = await tryRefreshToken();
        if (!refreshed) {
          useChatStore.getState().clearData();
          setIsLoading(false);
          return;
        }
      }

      try {
        const u = await api.me();
        if (!cancelled) setUser(u);
      } catch {
        clearTokens();
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    init();
    return () => { cancelled = true; };
  }, []);

  // Proactive token refresh every 10 minutes
  useEffect(() => {
    if (!user) return;
    refreshTimerRef.current = setInterval(
      () => { tryRefreshToken(); },
      10 * 60 * 1000,
    );
    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    };
  }, [user]);

  const login = useCallback(async (username: string, password: string) => {
    const data = await api.login(username, password);
    setTokens(data.access_token, data.refresh_token);
    setUser(data.user);
    router.replace("/");
  }, [router]);

  const register = useCallback(async (username: string, password: string) => {
    const data = await api.register(username, password);
    setTokens(data.access_token, data.refresh_token);
    setUser(data.user);
    router.replace("/");
  }, [router]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // ignore
    }
    clearTokens();
    setUser(null);
    conversationsLoadedRef.current = false;
    useChatStore.getState().clearData();
    router.replace("/login");
  }, [router]);

  // Load conversations exactly once when user becomes authenticated.
  // Uses getState() to avoid subscribing to the Zustand store — no re-renders.
  const conversationsLoadedRef = useRef(false);
  useEffect(() => {
    if (user && !conversationsLoadedRef.current) {
      conversationsLoadedRef.current = true;
      useChatStore.getState().loadConversations();
    }
  }, [user]);

  // Memoize context value to prevent cascading re-renders of all consumers
  const ctx = useMemo<AuthContextValue>(
    () => ({ user, isLoading, isAuthenticated: !!user, login, register, logout }),
    [user, isLoading, login, register, logout],
  );

  return (
    <AuthContext.Provider value={ctx}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
