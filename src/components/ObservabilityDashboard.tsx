"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Check,
  CircleDot,
  Clock3,
  Cpu,
  DatabaseZap,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  api,
  type ObservabilityOverview,
  type ObservabilityVersion,
  type RunTrace,
  type TraceTimelineEvent,
} from "@/lib/api";

const VERSION_COLORS = ["var(--accent)", "var(--signal)", "#6f8d6c"];
const SEARCH_MODE_LABELS = {
  auto: "普通对话",
  web: "联网检索",
  deep: "深度检索",
};

const numberFormat = new Intl.NumberFormat("zh-CN");
const compactFormat = new Intl.NumberFormat("zh-CN", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatNumber(value: number) {
  return numberFormat.format(value || 0);
}

function formatCompact(value: number) {
  return compactFormat.format(value || 0);
}

function formatDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function percent(value: number | null) {
  return value === null ? "待评测" : `${(value * 100).toFixed(1)}%`;
}

function MetricDelta({ current, previous }: { current: number; previous?: number }) {
  if (!previous || current === previous) {
    return <span className="text-[var(--fg-subtle)]">基线</span>;
  }
  const delta = ((current - previous) / previous) * 100;
  const improving = delta < 0;
  const Icon = improving ? ArrowDownRight : ArrowUpRight;
  return (
    <span className={improving ? "text-[var(--accent)]" : "text-[var(--signal)]"}>
      <Icon className="mr-0.5 inline h-3 w-3" />
      {Math.abs(delta).toFixed(1)}%
    </span>
  );
}

function VersionLegend({ versions }: { versions: ObservabilityVersion[] }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2">
      {versions.map((version, index) => (
        <div key={version.id} className="flex items-center gap-2 text-xs">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: VERSION_COLORS[index] }}
          />
          <span className="font-medium text-[var(--fg)]">{version.label}</span>
          <span className="font-mono text-[10px] text-[var(--fg-subtle)]">
            {version.model.replace("deepseek-", "")}
          </span>
        </div>
      ))}
    </div>
  );
}

function ComparisonBar({
  value,
  max,
  color,
}: {
  value: number;
  max: number;
  color: string;
}) {
  const width = max > 0 ? Math.max(3, (value / max) * 100) : 0;
  return (
    <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-subtle)]">
      <div
        className="h-full rounded-full transition-[width] duration-700 ease-out"
        style={{ width: `${width}%`, backgroundColor: color }}
      />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="mx-auto flex min-h-[70vh] max-w-3xl items-center px-6 py-16">
      <div className="w-full border-y border-[var(--border-strong)] py-12">
        <div className="mb-8 flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]">
            <Activity className="h-5 w-5" />
          </span>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--accent)]">
              Evaluation workspace ready
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">等待第一组评测结果</h1>
          </div>
        </div>
        <p className="max-w-2xl text-sm leading-7 text-[var(--fg-muted)]">
          被测 Chatbot 的 trace 采集已经启用。执行固定回归 Case 集后，这里会按优化版本汇总 Token、模型与工具调用、耗时、回答质量和完整事件时间线。
        </p>
        <ol className="mt-8 grid gap-px overflow-hidden border border-[var(--border)] bg-[var(--border)] md:grid-cols-3">
          {[
            ["01", "准备 Case", "为固定问题集分配稳定 Case ID"],
            ["02", "执行回归", "让被测 Chatbot 完整跑完测试集"],
            ["03", "评测质量", "在此判定并比较每个优化版本"],
          ].map(([step, title, body]) => (
            <li key={step} className="bg-[var(--bg-elev)] p-5">
              <span className="font-mono text-[10px] text-[var(--fg-subtle)]">{step}</span>
              <p className="mt-3 text-sm font-semibold">{title}</p>
              <p className="mt-1 text-xs leading-5 text-[var(--fg-muted)]">{body}</p>
            </li>
          ))}
        </ol>
        <div className="mt-8 inline-flex items-center gap-2 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-elev)] px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-muted)]">
          <CircleDot className="h-3.5 w-3.5 text-[var(--accent)]" />
          Collector online · no eval runs yet
        </div>
      </div>
    </div>
  );
}

function TimelineItem({ event }: { event: TraceTimelineEvent }) {
  const error = event.status === "error";
  const metadata = event.metadata;
  const toolDetail = event.type === "tool.end"
    ? [
        typeof metadata.duration_ms === "number"
          ? formatDuration(metadata.duration_ms)
          : null,
        typeof metadata.output_chars === "number"
          ? `${formatCompact(metadata.output_chars)} chars`
          : null,
        metadata.output_truncated ? "已裁剪" : null,
        typeof metadata.rejection_reason === "string"
          ? metadata.rejection_reason
          : null,
        typeof metadata.timeout_reason === "string"
          ? metadata.timeout_reason
          : null,
      ].filter(Boolean).join(" · ")
    : "";
  return (
    <li className="group relative grid grid-cols-[44px_1fr] gap-3 pb-5 last:pb-0">
      <div className="relative flex justify-center">
        <span
          className={`relative z-10 mt-1 h-2.5 w-2.5 rounded-full border-2 border-[var(--bg-elev)] ${
            error ? "bg-[var(--signal)]" : "bg-[var(--accent)]"
          }`}
        />
        <span className="absolute bottom-[-4px] top-3 w-px bg-[var(--border)] group-last:hidden" />
      </div>
      <div className="min-w-0">
        <div className="flex items-start justify-between gap-3">
          <p className="text-xs font-medium leading-5 text-[var(--fg)]">{event.label}</p>
          <span className="shrink-0 font-mono text-[9px] text-[var(--fg-subtle)]">
            +{formatDuration(event.at_ms)}
          </span>
        </div>
        <p className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--fg-subtle)]">
          {event.type}
        </p>
        {toolDetail ? (
          <p className="mt-1 text-[10px] leading-4 text-[var(--fg-muted)]">
            {toolDetail}
          </p>
        ) : null}
      </div>
    </li>
  );
}

function RunInspector({
  run,
  onEvaluated,
}: {
  run: RunTrace;
  onEvaluated: (run: RunTrace) => void;
}) {
  const [note, setNote] = useState(run.evaluation?.note ?? "");
  const [caseId, setCaseId] = useState(run.evaluation?.case_id ?? "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    setNote(run.evaluation?.note ?? "");
    setCaseId(run.evaluation?.case_id ?? "");
    setSaveError("");
  }, [run.run_id, run.evaluation]);

  async function saveEvaluation(passed: boolean | null) {
    setSaving(true);
    setSaveError("");
    try {
      const updated = await api.evaluateRun(run.run_id, {
        passed,
        note,
        case_id: caseId,
      });
      onEvaluated(updated);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <aside className="border-l border-[var(--border)] bg-[var(--bg-elev)] xl:sticky xl:top-0 xl:h-[calc(100vh)] xl:overflow-y-auto">
      <div className="border-b border-[var(--border)] px-5 py-5">
        <div className="flex items-center justify-between gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">
            Run inspector
          </span>
          <span
            className={`rounded-full px-2 py-1 text-[10px] font-semibold ${
              run.status === "success"
                ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "bg-[var(--signal-soft)] text-[var(--signal)]"
            }`}
          >
            {run.status}
          </span>
        </div>
        <p className="mt-3 truncate font-mono text-sm font-semibold text-[var(--fg)]">
          {run.run_id.slice(0, 12)}
        </p>
        <p className="mt-1 truncate text-xs text-[var(--fg-muted)]">
          {run.conversation?.title || "未命名对话"}
        </p>
      </div>

      <div className="grid grid-cols-2 border-b border-[var(--border)]">
        {[
          ["Token", formatCompact(run.metrics.total_tokens)],
          ["耗时", formatDuration(run.duration_ms)],
          ["LLM 调用", formatNumber(run.metrics.llm_calls)],
          ["工具调用", formatNumber(run.metrics.tool_calls)],
          ["策略拒绝", formatNumber(run.metrics.tool_rejections ?? 0)],
          ["工具超时", formatNumber(run.metrics.tool_timeouts ?? 0)],
          ["工具输出", `${formatCompact(run.metrics.tool_output_chars ?? 0)} 字符`],
          ["结果裁剪", formatNumber(run.metrics.tool_truncations ?? 0)],
        ].map(([label, value], index) => (
          <div
            key={label}
            className={`px-5 py-4 ${index % 2 === 0 ? "border-r" : ""} ${index < 6 ? "border-b" : ""} border-[var(--border)]`}
          >
            <p className="text-[10px] text-[var(--fg-subtle)]">{label}</p>
            <p className="mt-1 font-mono text-lg font-semibold">{value}</p>
          </div>
        ))}
      </div>

      <div className="border-b border-[var(--border)] px-5 py-5">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold">质量判定</h3>
          <span className="text-[10px] text-[var(--fg-subtle)]">
            {run.evaluation ? "已评测" : "待评测"}
          </span>
        </div>
        <input
          value={caseId}
          onChange={(event) => setCaseId(event.target.value)}
          placeholder="Case ID（可选）"
          className="focus-ring w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 font-mono text-xs outline-none"
        />
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="判定依据或失败原因（可选）"
          rows={3}
          className="focus-ring mt-2 w-full resize-none rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-xs leading-5 outline-none"
        />
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={saving}
            onClick={() => saveEvaluation(true)}
            className="focus-ring flex items-center justify-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            通过
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => saveEvaluation(false)}
            className="focus-ring flex items-center justify-center gap-1.5 rounded-lg bg-[var(--signal)] px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" />
            未通过
          </button>
        </div>
        {run.evaluation ? (
          <button
            type="button"
            disabled={saving}
            onClick={() => saveEvaluation(null)}
            className="mt-2 flex w-full items-center justify-center gap-1.5 py-1 text-[10px] text-[var(--fg-subtle)] hover:text-[var(--fg)]"
          >
            <RotateCcw className="h-3 w-3" />
            清除判定
          </button>
        ) : null}
        {saveError ? <p className="mt-2 text-xs text-[var(--signal)]">{saveError}</p> : null}
      </div>

      <div className="px-5 py-5">
        <div className="mb-5 flex items-center justify-between">
          <h3 className="text-sm font-semibold">事件回放</h3>
          <span className="font-mono text-[10px] text-[var(--fg-subtle)]">
            {run.timeline.length} EVENTS
          </span>
        </div>
        {run.timeline.length > 0 ? (
          <ol>
            {run.timeline.map((event) => (
              <TimelineItem key={`${event.id}-${event.at_ms}-${event.type}`} event={event} />
            ))}
          </ol>
        ) : (
          <p className="text-xs leading-5 text-[var(--fg-subtle)]">这条运行没有记录阶段事件。</p>
        )}
      </div>
    </aside>
  );
}

export function ObservabilityDashboard() {
  const [overview, setOverview] = useState<ObservabilityOverview | null>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const loadOverview = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = await api.getObservabilityOverview();
      setOverview(data);
      setSelectedRunId((current) => current || data.runs[0]?.run_id || "");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.getObservabilityOverview()
      .then((data) => {
        if (cancelled) return;
        setOverview(data);
        setSelectedRunId(data.runs[0]?.run_id || "");
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const versions = overview?.versions.slice(0, 3) ?? [];
  const selectedRun = overview?.runs.find((run) => run.run_id === selectedRunId)
    ?? overview?.runs[0];
  const totals = useMemo(() => {
    const runs = overview?.runs ?? [];
    let evaluated = 0;
    let passed = 0;
    let tokens = 0;
    let duration = 0;
    for (const run of runs) {
      tokens += run.metrics.total_tokens;
      duration += run.duration_ms;
      if (run.evaluation) {
        evaluated += 1;
        passed += Number(run.evaluation.passed);
      }
    }
    return {
      runs: runs.length,
      evaluated,
      passed,
      avgTokens: runs.length ? Math.round(tokens / runs.length) : 0,
      avgDuration: runs.length ? Math.round(duration / runs.length) : 0,
    };
  }, [overview]);

  function handleEvaluated(updated: RunTrace) {
    setOverview((current) => {
      if (!current) return current;
      const runs = current.runs.map((run) =>
        run.run_id === updated.run_id ? updated : run,
      );
      return { runs, versions: current.versions };
    });
    loadOverview(true);
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="flex items-center gap-3 text-sm text-[var(--fg-muted)]">
          <RefreshCw className="h-4 w-4 animate-spin" />
          正在整理运行档案…
        </div>
      </div>
    );
  }

  if (error && !overview) {
    return (
      <div className="flex flex-1 items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <X className="mx-auto h-6 w-6 text-[var(--signal)]" />
          <p className="mt-3 text-sm font-semibold">运行档案暂时不可用</p>
          <p className="mt-1 text-xs text-[var(--fg-muted)]">{error}</p>
          <button
            type="button"
            onClick={() => loadOverview()}
            className="focus-ring mt-4 rounded-lg border border-[var(--border-strong)] px-4 py-2 text-xs font-semibold"
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }

  if (!overview || overview.runs.length === 0) return <EmptyState />;

  const maxTotalTokens = Math.max(...versions.map((version) => version.total_tokens), 1);
  const maxAvgTokens = Math.max(...versions.map((version) => version.avg_tokens), 1);
  const previousVersion = versions[1];

  return (
    <div className="grid h-full min-w-0 flex-1 xl:grid-cols-[minmax(0,1fr)_340px]">
      <section className="scrollbar-thin min-w-0 overflow-y-auto">
        <header className="border-b border-[var(--border)] px-6 py-5 lg:px-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--accent)]">
                <CircleDot className="h-3 w-3" />
                Chatbot evaluation ledger
              </div>
              <h1 className="mt-2 text-2xl font-semibold tracking-[-0.035em] text-[var(--fg)]">
                评测总览与优化对比
              </h1>
              <p className="mt-1.5 max-w-xl text-xs leading-5 text-[var(--fg-muted)]">
                每次优化由代码指纹形成独立版本；运行、成本与质量判定均可回放。
              </p>
            </div>
            <button
              type="button"
              onClick={() => loadOverview(true)}
              disabled={refreshing}
              className="focus-ring flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-elev)] px-3 py-2 text-xs font-medium shadow-[var(--shadow-sm)] disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
              刷新
            </button>
          </div>
        </header>

        <div className="grid grid-cols-2 border-b border-[var(--border)] lg:grid-cols-4">
          {[
            ["已记录运行", formatNumber(totals.runs), <DatabaseZap key="runs" className="h-4 w-4" />],
            ["已评测", `${totals.evaluated} / ${totals.runs}`, <ShieldCheck key="evaluated" className="h-4 w-4" />],
            ["平均 Token", formatCompact(totals.avgTokens), <Cpu key="tokens" className="h-4 w-4" />],
            ["平均耗时", formatDuration(totals.avgDuration), <Clock3 key="duration" className="h-4 w-4" />],
          ].map(([label, value, icon], index) => (
            <div
              key={String(label)}
              className={`observability-reveal px-6 py-5 lg:px-8 ${index < 3 ? "border-r" : ""} ${index < 2 ? "border-b lg:border-b-0" : ""} border-[var(--border)]`}
              style={{ animationDelay: `${index * 55}ms` }}
            >
              <div className="flex items-center justify-between text-[var(--fg-subtle)]">
                <p className="text-[10px] uppercase tracking-[0.1em]">{label}</p>
                {icon}
              </div>
              <p className="mt-3 font-mono text-xl font-semibold tracking-tight">{value}</p>
            </div>
          ))}
        </div>

        <div className="space-y-10 px-6 py-8 lg:px-8">
          <section className="observability-reveal" style={{ animationDelay: "180ms" }}>
            <div className="flex flex-wrap items-end justify-between gap-4 border-b border-[var(--border-strong)] pb-4">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">01 / Versions</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">最近三版运行成本</h2>
              </div>
              <VersionLegend versions={versions} />
            </div>

            <div className="mt-6 space-y-6">
              {versions.map((version, index) => (
                <div key={version.id} className="grid gap-3 md:grid-cols-[180px_1fr_88px] md:items-center">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: VERSION_COLORS[index] }} />
                      <span className="truncate text-sm font-semibold">{version.label}</span>
                    </div>
                    <p className="mt-1 truncate pl-4 font-mono text-[9px] text-[var(--fg-subtle)]">
                      {version.code_fingerprint.slice(0, 16)}
                    </p>
                  </div>
                  <ComparisonBar value={version.total_tokens} max={maxTotalTokens} color={VERSION_COLORS[index]} />
                  <div className="text-left md:text-right">
                    <p className="font-mono text-sm font-semibold">{formatCompact(version.total_tokens)}</p>
                    <p className="text-[9px] text-[var(--fg-subtle)]">{version.runs} RUNS</p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="observability-reveal" style={{ animationDelay: "220ms" }}>
            <div className="border-b border-[var(--border-strong)] pb-4">
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">02 / Same class</p>
              <h2 className="mt-1 text-lg font-semibold tracking-tight">同类问题整轮 Token</h2>
            </div>
            <div className="mt-5 grid gap-5 lg:grid-cols-3">
              {(["auto", "web", "deep"] as const).map((categoryId) => {
                const categoryValues = versions.map((version) =>
                  version.categories.find((category) => category.id === categoryId)?.avg_tokens ?? 0,
                );
                const categoryMax = Math.max(...categoryValues, 1);
                return (
                  <div key={categoryId} className="border-t-2 border-[var(--border-strong)] bg-[var(--bg-elev)] px-4 py-4 shadow-[var(--shadow-sm)]">
                    <div className="mb-4 flex items-center justify-between">
                      <h3 className="text-sm font-semibold">{SEARCH_MODE_LABELS[categoryId]}</h3>
                      <span className="font-mono text-[9px] text-[var(--fg-subtle)]">AVG / RUN</span>
                    </div>
                    <div className="space-y-3.5">
                      {versions.map((version, index) => {
                        const category = version.categories.find((item) => item.id === categoryId);
                        const value = category?.avg_tokens ?? 0;
                        return (
                          <div key={version.id}>
                            <div className="mb-1.5 flex items-center justify-between gap-2 text-[10px]">
                              <span className="truncate text-[var(--fg-muted)]">{version.label}</span>
                              <span className="shrink-0 font-mono font-semibold text-[var(--fg)]">{formatCompact(value)}</span>
                            </div>
                            <ComparisonBar value={value} max={categoryMax} color={VERSION_COLORS[index]} />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="observability-reveal grid gap-8 lg:grid-cols-2" style={{ animationDelay: "260ms" }}>
            <div>
              <div className="border-b border-[var(--border-strong)] pb-3">
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">03 / Cost</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">单轮 Token 与耗时</h2>
              </div>
              <div className="mt-5 space-y-5">
                {versions.map((version, index) => (
                  <div key={version.id}>
                    <div className="mb-2 flex items-baseline justify-between gap-3">
                      <span className="truncate text-xs font-medium">{version.label}</span>
                      <span className="font-mono text-xs font-semibold">{formatCompact(version.avg_tokens)}</span>
                    </div>
                    <ComparisonBar value={version.avg_tokens} max={maxAvgTokens} color={VERSION_COLORS[index]} />
                    <div className="mt-1.5 flex justify-between text-[9px] text-[var(--fg-subtle)]">
                      <span>AVG TOKEN</span>
                      <span>{formatDuration(version.avg_duration_ms)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <div className="border-b border-[var(--border-strong)] pb-3">
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">04 / Expansion</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">执行与工具开销</h2>
              </div>
              <div className="mt-4 overflow-hidden border-y border-[var(--border)]">
                <div className="grid grid-cols-[1fr_52px_52px_58px_70px] py-2 text-[9px] uppercase tracking-[0.1em] text-[var(--fg-subtle)]">
                  <span>版本</span><span className="text-right">LLM</span><span className="text-right">工具</span><span className="text-right">拒绝</span><span className="text-right">工具耗时</span>
                </div>
                {versions.map((version, index) => (
                  <div key={version.id} className="grid grid-cols-[1fr_52px_52px_58px_70px] items-center border-t border-[var(--border)] py-3 text-xs">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: VERSION_COLORS[index] }} />
                      <span className="truncate">{version.label}</span>
                    </span>
                    <span className="text-right font-mono font-semibold">{version.llm_calls}</span>
                    <span className="text-right font-mono font-semibold">{version.tool_calls}</span>
                    <span className="text-right font-mono font-semibold">{version.tool_rejections ?? 0}</span>
                    <span className="text-right font-mono font-semibold">{formatDuration(version.avg_tool_duration_ms ?? 0)}</span>
                  </div>
                ))}
              </div>
              {versions[0] ? (
                <div className="mt-4 space-y-2 rounded-lg bg-[var(--bg-subtle)] px-3 py-2 text-[10px]">
                  <div className="flex items-center justify-between">
                    <span className="text-[var(--fg-muted)]">当前版整轮耗时对比上一版</span>
                    <MetricDelta current={versions[0].avg_duration_ms} previous={previousVersion?.avg_duration_ms} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[var(--fg-muted)]">单次工具输出字符对比上一版</span>
                    <MetricDelta current={versions[0].avg_tool_output_chars ?? 0} previous={previousVersion?.avg_tool_output_chars} />
                  </div>
                </div>
              ) : null}
            </div>
          </section>

          <section className="observability-reveal" style={{ animationDelay: "320ms" }}>
            <div className="flex flex-wrap items-end justify-between gap-4 border-b border-[var(--border-strong)] pb-4">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">05 / Quality</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">回答通过率</h2>
              </div>
              <p className="max-w-sm text-right text-[10px] leading-4 text-[var(--fg-subtle)]">
                只统计人工或外部 Judge 已判定的运行，传输成功不等于回答通过。
              </p>
            </div>
            <div className="mt-6 space-y-5">
              {versions.map((version, index) => (
                <div key={version.id} className="grid gap-2 md:grid-cols-[180px_1fr_120px] md:items-center">
                  <span className="truncate text-sm font-medium">{version.label}</span>
                  <ComparisonBar value={version.pass_rate ?? 0} max={1} color={VERSION_COLORS[index]} />
                  <p className="font-mono text-sm font-semibold md:text-right">
                    {version.pass_rate === null
                      ? "待评测"
                      : `${version.passed_runs} / ${version.evaluated_runs} · ${percent(version.pass_rate)}`}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section className="observability-reveal pb-8" style={{ animationDelay: "380ms" }}>
            <div className="flex items-end justify-between border-b border-[var(--border-strong)] pb-4">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">06 / Runs</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">最近运行</h2>
              </div>
              <span className="font-mono text-[10px] text-[var(--fg-subtle)]">CLICK TO REPLAY</span>
            </div>
            <div className="mt-2 divide-y divide-[var(--border)]">
              {overview.runs.slice(0, 40).map((run) => {
                const selected = run.run_id === selectedRun?.run_id;
                return (
                  <button
                    type="button"
                    key={run.run_id}
                    onClick={() => setSelectedRunId(run.run_id)}
                    className={`focus-ring grid w-full grid-cols-[1fr_auto] items-center gap-4 px-2 py-4 text-left transition-colors md:grid-cols-[1fr_110px_90px_90px] ${
                      selected ? "bg-[var(--accent-soft)]" : "hover:bg-[var(--sidebar-item-hover)]"
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${run.status === "success" ? "bg-[var(--accent)]" : "bg-[var(--signal)]"}`} />
                        <span className="truncate text-xs font-semibold">{run.conversation?.title || "未命名对话"}</span>
                      </span>
                      <span className="mt-1 block truncate pl-3.5 font-mono text-[9px] text-[var(--fg-subtle)]">
                        {run.version.label} · {run.run_id.slice(0, 10)}
                      </span>
                    </span>
                    <span className="hidden text-right text-[10px] text-[var(--fg-muted)] md:block">
                      {SEARCH_MODE_LABELS[run.search_mode]}
                    </span>
                    <span className="hidden text-right font-mono text-[10px] text-[var(--fg-muted)] md:block">
                      {formatCompact(run.metrics.total_tokens)} T
                    </span>
                    <span className="text-right">
                      <span className="block font-mono text-[10px] text-[var(--fg-muted)]">{formatTime(run.started_at)}</span>
                      <span className={`mt-1 inline-flex items-center gap-1 text-[9px] font-semibold ${run.evaluation ? (run.evaluation.passed ? "text-[var(--accent)]" : "text-[var(--signal)]") : "text-[var(--fg-subtle)]"}`}>
                        {run.evaluation ? (run.evaluation.passed ? <Check className="h-2.5 w-2.5" /> : <X className="h-2.5 w-2.5" />) : <Search className="h-2.5 w-2.5" />}
                        {run.evaluation ? (run.evaluation.passed ? "PASS" : "FAIL") : "PENDING"}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        </div>
      </section>

      {selectedRun ? (
        <RunInspector run={selectedRun} onEvaluated={handleEvaluated} />
      ) : null}
    </div>
  );
}
