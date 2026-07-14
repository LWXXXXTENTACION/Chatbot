"use client";

import { useState } from "react";
import {
  Calculator,
  ChevronDown,
  CloudSun,
  Globe,
  Loader2,
  CircleCheck,
  CircleX,
  Hourglass,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ToolState, ToolPartBase } from "@/lib/types";

// ============================================================
// Tool-specific metadata & result formatting
// ============================================================

interface ToolMeta {
  label: string;
  icon: LucideIcon;
  formatResult?: (output: Record<string, unknown>) => React.ReactNode;
}

const TOOL_META: Record<string, ToolMeta> = {
  getWeather: {
    label: "查询天气",
    icon: CloudSun,
    formatResult: (output) => (
      <div className="flex items-center gap-4 py-1">
        <div className="flex items-baseline gap-1">
          <span className="text-[28px] font-bold tracking-tight text-[var(--fg)]">
            {String(output.tempC ?? "—")}
          </span>
          <span className="text-[14px] font-medium text-[var(--fg-muted)]">°C</span>
        </div>
        <div className="h-8 w-px bg-[var(--border)]" />
        <div className="flex flex-col gap-0.5 text-[12.5px]">
          <span className="font-medium text-[var(--fg)]">
            {String(output.condition ?? "—")}
          </span>
          <span className="text-[var(--fg-muted)]">
            💧 {String(output.humidity ?? "—")}%
          </span>
        </div>
      </div>
    ),
  },
  calculate: {
    label: "计算",
    icon: Calculator,
    formatResult: (output) => (
      <div className="space-y-1.5 py-0.5">
        <code className="block text-[12.5px] text-[var(--fg-muted)]">
          {String(output.expression ?? "")}
        </code>
        <span className="text-[18px] font-bold tracking-tight text-[var(--fg)]">
          = {String(output.result ?? "—")}
        </span>
      </div>
    ),
  },
  create_artifact: { label: "创建工件", icon: Wrench },
  web_search: {
    label: "搜索网页",
    icon: Globe,
    formatResult: (output) => (
      <div className="space-y-1.5 py-0.5">
        <p className="text-[12.5px] font-medium text-[var(--fg)]">
          查询：{String(output.query ?? "")}
        </p>
        <p className="text-[11.5px] text-[var(--fg-muted)]">
          找到 {(output.results as unknown[])?.length ?? 0} 条结果
        </p>
      </div>
    ),
  },
};

function toolName(type: string) {
  return type.startsWith("tool-") ? type.slice(5) : type;
}

// ============================================================
// Component
// ============================================================

export function ToolInvocation({ part }: { part: ToolPartBase }) {
  const name = toolName(part.type);
  const meta = TOOL_META[name] ?? { label: name, icon: Wrench };
  const Icon = meta.icon;
  const [open, setOpen] = useState(false);

  const inputStreaming = part.state === "input-streaming";
  const executing = part.state === "input-available";
  const awaitingApproval = part.state === "approval-requested";
  const errored = part.state === "output-error";
  const done = part.state === "output-available";

  const running = inputStreaming || executing;

  return (
    <div className="w-full overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)]/70 text-[13px] shadow-[var(--shadow-sm)] backdrop-blur">
      {/* Header button */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="focus-ring flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
      >
        {/* Icon */}
        <div
          className={`flex h-6 w-6 items-center justify-center rounded-lg transition-colors duration-300 ${
            awaitingApproval
              ? "bg-amber-500/12 text-amber-500"
              : errored
                ? "bg-red-500/12 text-red-500"
                : done
                  ? "bg-emerald-500/12 text-emerald-500"
                  : "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
          }`}
        >
          <Icon className="h-3.5 w-3.5" strokeWidth={2.3} />
        </div>

        {/* Label */}
        <span className="font-medium text-[var(--fg)]">{meta.label}</span>

        {/* Status badge */}
        <span className="ml-1 inline-flex items-center gap-1 text-[11.5px]">
          {awaitingApproval ? (
            <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
              <Hourglass className="h-3 w-3" />
              <span>等待确认</span>
            </span>
          ) : inputStreaming ? (
            <span className="inline-flex items-center gap-1 text-[var(--accent-strong)]">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span className="shimmer-text">接收参数中…</span>
            </span>
          ) : executing ? (
            <span className="inline-flex items-center gap-1 text-[var(--accent-strong)]">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>执行中…</span>
            </span>
          ) : errored ? (
            <span className="inline-flex items-center gap-1 text-red-500">
              <CircleX className="h-3 w-3" />
              <span>失败</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-emerald-500">
              <CircleCheck className="h-3 w-3 scale-in" />
              <span>完成</span>
            </span>
          )}
        </span>

        {/* Chevron */}
        <ChevronDown
          className={`ml-auto h-4 w-4 text-[var(--fg-subtle)] transition-transform duration-300 ${
            open ? "" : "-rotate-90"
          }`}
        />
      </button>

      {/* Expandable body with animation */}
      <div
        className={`overflow-hidden transition-all duration-300 ease-out ${
          open ? "max-h-[600px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="space-y-2.5 border-t border-[var(--border)] px-3.5 py-3">
          {/* Parameters */}
          {part.input != null ? (
            <Section label="参数">
              <CodePeek value={part.input} />
            </Section>
          ) : null}

          {/* Result — prefer tool-specific formatter, fall back to CodePeek */}
          {done && part.output != null ? (
            <Section label="结果">
              {meta.formatResult ? (
                <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)] px-3.5 py-2.5">
                  {meta.formatResult(
                    (typeof part.output === "object" && part.output !== null
                      ? part.output
                      : {}) as Record<string, unknown>,
                  )}
                </div>
              ) : (
                <CodePeek value={part.output} />
              )}
            </Section>
          ) : null}

          {/* Error */}
          {errored ? (
            <Section label="错误">
              <p className="rounded-lg border border-red-200/60 bg-red-50/50 px-3 py-2 text-[12px] text-red-600 dark:border-red-900/30 dark:bg-red-950/20 dark:text-red-400">
                {part.errorText || "未知错误"}
              </p>
            </Section>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// Sub-components
// ============================================================

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-1 text-[10.5px] font-medium uppercase tracking-[0.1em] text-[var(--fg-subtle)]">
        {label}
      </p>
      {children}
    </div>
  );
}

function CodePeek({ value }: { value: unknown }) {
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className="scrollbar-thin max-h-56 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)] px-3 py-2 font-mono text-[11.5px] leading-relaxed text-[var(--fg-muted)]">
      {text}
    </pre>
  );
}
