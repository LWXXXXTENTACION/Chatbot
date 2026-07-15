"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/providers/AuthProvider";
import { ThemeToggle } from "@/components/ThemeToggle";

type Tab = "login" | "register";

export default function LoginPage() {
  const { isAuthenticated, isLoading, login, register } = useAuth();
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Already authenticated → redirect to home (must be in useEffect, not during render)
  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.replace("/");
    }
  }, [isLoading, isAuthenticated, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!username.trim()) {
      setError("请输入用户名");
      return;
    }
    if (password.length < 6) {
      setError("密码至少需要 6 个字符");
      return;
    }
    if (tab === "register" && password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }

    setSubmitting(true);
    try {
      if (tab === "login") {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[var(--fg-muted)]">
        加载中…
      </div>
    );
  }

  if (isAuthenticated) return null;

  return (
    <div className="app-bg relative flex h-full items-center justify-center bg-[var(--bg)]">
      <div className="absolute right-5 top-5">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)] p-8 shadow-lg">
        {/* Header */}
        <h1 className="mb-2 text-center text-xl font-bold text-[var(--fg)]">
          DeepSeek Chat Studio
        </h1>
        <p className="mb-6 text-center text-sm text-[var(--fg-muted)]">
          专注于检索、推理与创作的对话工作台
        </p>

        {/* Tabs */}
        <div className="mb-6 flex rounded-lg bg-[var(--bg-subtle)] p-1">
          <button
            type="button"
            onClick={() => { setTab("login"); setError(""); }}
            className={`flex-1 rounded-md py-2 text-sm font-medium transition-colors ${
              tab === "login"
                ? "bg-[var(--bg-elev)] text-[var(--fg)] shadow-sm"
                : "text-[var(--fg-muted)] hover:text-[var(--fg)]"
            }`}
          >
            登录
          </button>
          <button
            type="button"
            onClick={() => { setTab("register"); setError(""); }}
            className={`flex-1 rounded-md py-2 text-sm font-medium transition-colors ${
              tab === "register"
                ? "bg-[var(--bg-elev)] text-[var(--fg)] shadow-sm"
                : "text-[var(--fg-muted)] hover:text-[var(--fg)]"
            }`}
          >
            注册
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label
              htmlFor="username"
              className="mb-1.5 block text-sm font-medium text-[var(--fg)]"
            >
              用户名
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户名"
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2.5 text-sm text-[var(--fg)] placeholder:text-[var(--fg-subtle)] outline-none focus:ring-2 focus:ring-[var(--accent)] transition-shadow"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="mb-1.5 block text-sm font-medium text-[var(--fg)]"
            >
              密码
            </label>
            <input
              id="password"
              type="password"
              autoComplete={tab === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码（至少 6 位）"
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2.5 text-sm text-[var(--fg)] placeholder:text-[var(--fg-subtle)] outline-none focus:ring-2 focus:ring-[var(--accent)] transition-shadow"
            />
          </div>

          {tab === "register" && (
            <div>
              <label
                htmlFor="confirmPassword"
                className="mb-1.5 block text-sm font-medium text-[var(--fg)]"
              >
                确认密码
              </label>
              <input
                id="confirmPassword"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="请再次输入密码"
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2.5 text-sm text-[var(--fg)] placeholder:text-[var(--fg-subtle)] outline-none focus:ring-2 focus:ring-[var(--accent)] transition-shadow"
              />
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-400">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="focus-ring mt-2 w-full rounded-lg bg-[var(--accent)] px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-strong)] disabled:opacity-50"
          >
            {submitting
              ? "处理中…"
              : tab === "login"
                ? "登录"
                : "注册"}
          </button>
        </form>

        <p className="mt-6 text-center text-xs text-[var(--fg-subtle)]">
          DeepSeek Chat Studio · 多模型对话工作台
        </p>
      </div>
    </div>
  );
}
