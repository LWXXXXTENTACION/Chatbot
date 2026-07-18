"use client";

import { type FormEvent, useState } from "react";
import {
  Activity,
  FlaskConical,
  LogOut,
  Radio,
  ShieldCheck,
  Target,
} from "lucide-react";
import { ObservabilityDashboard } from "@/components/ObservabilityDashboard";
import { SSEPerformanceEval } from "@/components/SSEPerformanceEval";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api } from "@/lib/api";
import { clearTokens, setTokens } from "@/lib/auth";
import { useAuth } from "@/providers/AuthProvider";

function EvalLogin() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const data = await api.login(username, password);
      setTokens(data.access_token, data.refresh_token);
      window.location.replace("/evals");
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "登录失败");
      setSubmitting(false);
    }
  }

  return (
    <main className="eval-lab-bg grid min-h-full lg:grid-cols-[1.15fr_0.85fr]">
      <section className="relative hidden overflow-hidden border-r border-[var(--border)] px-12 py-10 lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--border-strong)] bg-[var(--bg-elev)] text-[var(--accent)]">
            <FlaskConical className="h-4 w-4" />
          </span>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-[var(--fg-subtle)]">Evaluation system</p>
            <p className="text-sm font-semibold">Chatbot Eval Lab</p>
          </div>
        </div>

        <div className="max-w-2xl pb-10">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--accent)]">Measure before optimize</p>
          <h1 className="mt-5 max-w-xl text-5xl font-semibold leading-[1.05] tracking-[-0.055em]">
            每一次优化，
            <br />都留下可以复验的证据。
          </h1>
          <p className="mt-6 max-w-lg text-sm leading-7 text-[var(--fg-muted)]">
            独立于 Chatbot 产品界面的测试台。固定 Case、绑定代码版本、回放单次运行，并同时衡量质量、成本与执行膨胀。
          </p>
          <div className="mt-10 grid max-w-lg grid-cols-3 gap-px overflow-hidden border border-[var(--border)] bg-[var(--border)]">
            {[
              [Target, "固定 Case"],
              [Activity, "全链路 Trace"],
              [ShieldCheck, "质量判定"],
            ].map(([Icon, label]) => {
              const FeatureIcon = Icon as typeof Target;
              return (
                <div key={String(label)} className="bg-[var(--bg-elev)] px-4 py-5">
                  <FeatureIcon className="h-4 w-4 text-[var(--accent)]" />
                  <p className="mt-4 text-xs font-semibold">{String(label)}</p>
                </div>
              );
            })}
          </div>
        </div>

        <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">
          Isolated evaluation surface · target: deepseek chatbot
        </p>
      </section>

      <section className="flex min-h-full items-center justify-center px-6 py-12">
        <form onSubmit={handleSubmit} className="w-full max-w-sm">
          <div className="mb-9 lg:hidden">
            <FlaskConical className="h-6 w-6 text-[var(--accent)]" />
            <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--fg-subtle)]">Chatbot Eval Lab</p>
          </div>
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--accent)]">Restricted workspace</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">登录评测系统</h2>
          <p className="mt-2 text-xs leading-5 text-[var(--fg-muted)]">
            使用现有 Chatbot 账号验证数据访问权限；登录后仅进入评测控制台。
          </p>

          <label className="mt-8 block text-xs font-medium text-[var(--fg-muted)]">
            用户名
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
              className="focus-ring mt-2 w-full rounded-lg border border-[var(--border-strong)] bg-[var(--bg-elev)] px-3 py-2.5 text-sm text-[var(--fg)] outline-none"
            />
          </label>
          <label className="mt-4 block text-xs font-medium text-[var(--fg-muted)]">
            密码
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
              className="focus-ring mt-2 w-full rounded-lg border border-[var(--border-strong)] bg-[var(--bg-elev)] px-3 py-2.5 text-sm text-[var(--fg)] outline-none"
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="focus-ring mt-6 flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--fg)] px-4 py-2.5 text-sm font-semibold text-[var(--bg-elev)] disabled:opacity-50"
          >
            <Radio className={`h-3.5 w-3.5 ${submitting ? "animate-pulse" : ""}`} />
            {submitting ? "正在验证…" : "进入 Eval Lab"}
          </button>
          {error ? <p className="mt-3 text-xs text-[var(--signal)]">{error}</p> : null}
        </form>
      </section>
    </main>
  );
}

export function EvaluationLab() {
  const { user, isLoading, isAuthenticated } = useAuth();
  const [surface, setSurface] = useState<"quality" | "transport">("quality");

  async function handleLogout() {
    try {
      await api.logout();
    } finally {
      clearTokens();
      window.location.replace("/evals");
    }
  }

  if (isLoading) {
    return (
      <main className="eval-lab-bg flex min-h-full items-center justify-center">
        <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-muted)]">
          <Radio className="h-3.5 w-3.5 animate-pulse text-[var(--accent)]" />
          Connecting evaluation workspace
        </div>
      </main>
    );
  }

  if (!isAuthenticated) return <EvalLogin />;

  return (
    <main className="eval-lab-bg flex h-full min-h-0 flex-col overflow-hidden">
      <header className="relative z-10 flex h-16 shrink-0 items-center border-b border-[var(--border-strong)] bg-[var(--bg-elev)]/90 px-5 backdrop-blur-xl lg:px-7">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--fg)] text-[var(--bg-elev)]">
            <FlaskConical className="h-3.5 w-3.5" />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold tracking-tight">Eval Lab</span>
              <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 font-mono text-[8px] uppercase tracking-[0.14em] text-[var(--accent-strong)]">Live</span>
            </div>
            <p className="font-mono text-[8px] uppercase tracking-[0.14em] text-[var(--fg-subtle)]">Chatbot quality system</p>
          </div>
        </div>

        <div className="ml-8 hidden items-center gap-2 border-l border-[var(--border)] pl-8 md:flex">
          <Target className="h-3.5 w-3.5 text-[var(--fg-subtle)]" />
          <span className="text-[10px] text-[var(--fg-muted)]">被测目标</span>
          <span className="font-mono text-[10px] font-semibold">DeepSeek Chatbot</span>
        </div>

        <nav className="ml-4 flex items-center rounded-lg bg-[var(--bg-subtle)] p-1 md:ml-8" aria-label="评测模块">
          {([
            ["quality", Target, "模型质量"],
            ["transport", Activity, "SSE 性能"],
          ] as const).map(([id, Icon, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSurface(id)}
              aria-pressed={surface === id}
              className={`focus-ring flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[10px] font-semibold transition-colors sm:px-3 ${
                surface === id
                  ? "bg-[var(--bg-elev)] text-[var(--fg)] shadow-[var(--shadow-sm)]"
                  : "text-[var(--fg-subtle)] hover:text-[var(--fg)]"
              }`}
            >
              <Icon className="h-3 w-3" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <div className="mr-2 hidden text-right sm:block">
            <p className="text-[10px] font-medium">{user?.username}</p>
            <p className="font-mono text-[8px] uppercase tracking-[0.12em] text-[var(--fg-subtle)]">Evaluator</p>
          </div>
          <ThemeToggle />
          <button
            type="button"
            onClick={handleLogout}
            className="focus-ring rounded-lg p-2 text-[var(--fg-muted)] hover:bg-[var(--bg-subtle)] hover:text-[var(--signal)]"
            aria-label="退出评测系统"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </header>
      <div className="relative z-10 min-h-0 flex-1">
        {surface === "quality" ? <ObservabilityDashboard /> : <SSEPerformanceEval />}
      </div>
    </main>
  );
}
