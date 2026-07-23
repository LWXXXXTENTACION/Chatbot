"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Check,
  Gauge,
  Radio,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import {
  runSSEPerformanceEval,
  type SSEPerformanceEvalResult,
} from "@/lib/sse-eval";

function formatMs(value: number) {
  return `${value.toFixed(value < 10 ? 2 : 1)} ms`;
}

function ComparisonRow({
  label,
  before,
  after,
  unit,
}: {
  label: string;
  before: number;
  after: number;
  unit: string;
}) {
  const max = Math.max(before, after, 1);
  return (
    <div className="border-t border-[var(--border)] py-4 first:border-t-0">
      <div className="mb-3 flex items-center justify-between gap-4">
        <p className="text-xs font-semibold">{label}</p>
        <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--fg-subtle)]">
          lower is better
        </span>
      </div>
      {[
        ["逐 delta 基线", before, "var(--signal)"],
        ["rAF 双缓冲", after, "var(--accent)"],
      ].map(([name, value, color]) => (
        <div key={String(name)} className="mt-2 grid grid-cols-[92px_1fr_92px] items-center gap-3">
          <span className="text-[10px] text-[var(--fg-muted)]">{name}</span>
          <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-subtle)]">
            <div
              className="h-full rounded-full transition-[width] duration-700"
              style={{ width: `${Math.max(1.5, (Number(value) / max) * 100)}%`, background: String(color) }}
            />
          </div>
          <span className="text-right font-mono text-[10px] font-semibold">
            {Number(value).toLocaleString("zh-CN")} {unit}
          </span>
        </div>
      ))}
    </div>
  );
}

export function SSEPerformanceEval() {
  const [result, setResult] = useState<SSEPerformanceEvalResult | null>(null);
  const [running, setRunning] = useState(false);

  const runEval = useCallback(() => {
    setRunning(true);
    requestAnimationFrame(() => {
      window.setTimeout(() => {
        void (async () => {
          try {
            setResult(await runSSEPerformanceEval());
          } finally {
            setRunning(false);
          }
        })();
      }, 0);
    });
  }, []);

  useEffect(() => {
    runEval();
  }, [runEval]);

  const passed = Boolean(
    result
      && result.protocol.parserPass
      && result.protocol.resumePass
      && result.robustness.corruptFramePass
      && result.robustness.watchdogPass
      && result.robustness.schedulerPass
      && result.robustness.sessionPersistencePass
      && result.robustness.unicodeBufferPass
      && result.robustness.streamingTextIntegrityPass
      && result.robustness.streamingMarkdownProjectionPass
      && result.robustness.duplicateReplayPass
      && result.robustness.artifactRestorePass
      && result.comparison.publicationReductionPct >= 95,
  );

  return (
    <div className="scrollbar-thin h-full overflow-y-auto">
      <header className="border-b border-[var(--border)] px-6 py-5 lg:px-8">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--accent)]">
              <Radio className="h-3 w-3" />
              Browser transport benchmark
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-[-0.035em]">SSE 流式链路对比</h1>
            <p className="mt-1.5 max-w-2xl text-xs leading-5 text-[var(--fg-muted)]">
              同一浏览器、同一负载下对比逐 delta 发布与 rAF 双缓冲，并用 1-byte TCP 分片和 Last-Event-ID 回放验证完整性。
            </p>
          </div>
          <button
            type="button"
            onClick={runEval}
            disabled={running}
            className="focus-ring flex items-center gap-2 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-elev)] px-4 py-2 text-xs font-semibold shadow-[var(--shadow-sm)] disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${running ? "animate-spin" : ""}`} />
            {running ? "评测中" : "重新评测"}
          </button>
        </div>
      </header>

      {!result ? (
        <div className="flex min-h-[55vh] items-center justify-center">
          <div className="flex items-center gap-3 text-xs text-[var(--fg-muted)]">
            <Gauge className="h-4 w-4 animate-pulse text-[var(--accent)]" />
            正在回放 30,000 个流式增量…
          </div>
        </div>
      ) : (
        <div className="px-6 py-7 lg:px-8">
          <section className="grid overflow-hidden border border-[var(--border)] bg-[var(--border)] sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["React 发布减少", `${result.comparison.publicationReductionPct}%`, Activity],
              ["主线程加速", `${result.comparison.pipelineSpeedup}×`, Gauge],
              ["拆包事件", `${result.protocol.decodedEvents} / ${result.protocol.decodedEvents}`, Radio],
              ["续传完整性", result.protocol.resumePass ? "PASS" : "FAIL", ShieldCheck],
            ].map(([label, value, Icon], index) => {
              const MetricIcon = Icon as typeof Activity;
              return (
                <div
                  key={String(label)}
                  className="observability-reveal bg-[var(--bg-elev)] px-5 py-5"
                  style={{ animationDelay: `${index * 55}ms` }}
                >
                  <div className="flex items-center justify-between text-[var(--fg-subtle)]">
                    <span className="text-[9px] uppercase tracking-[0.12em]">{String(label)}</span>
                    <MetricIcon className="h-3.5 w-3.5" />
                  </div>
                  <p className="mt-3 font-mono text-2xl font-semibold tracking-tight">{String(value)}</p>
                </div>
              );
            })}
          </section>

          <div className="mt-8 grid gap-8 xl:grid-cols-[minmax(0,1.25fr)_minmax(300px,0.75fr)]">
            <section className="border-t-2 border-[var(--fg)] bg-[var(--bg-elev)] px-5 py-4 shadow-[var(--shadow-sm)]">
              <div className="flex items-end justify-between gap-4 border-b border-[var(--border-strong)] pb-4">
                <div>
                  <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">01 / Throughput</p>
                  <h2 className="mt-1 text-base font-semibold">渲染压力与摄取耗时</h2>
                </div>
                <p className="font-mono text-[9px] text-[var(--fg-subtle)]">
                  {result.workload.deltas.toLocaleString("zh-CN")} DELTAS · {result.workload.characters.toLocaleString("zh-CN")} CHARS
                </p>
              </div>
              <ComparisonRow
                label="状态发布次数"
                before={result.legacy.publications}
                after={result.optimized.publications}
                unit="次"
              />
              <ComparisonRow
                label="主线程流水线中位耗时"
                before={result.legacy.pipelineMs}
                after={result.optimized.pipelineMs}
                unit="ms"
              />
              <p className="border-t border-[var(--border)] pt-4 text-[10px] leading-5 text-[var(--fg-subtle)]">
                流水线耗时取 7 次运行中位数，包含增量合并与 UI 文本快照投影；状态发布按每 {result.workload.deltasPerFrame} 个 delta 合并为一个动画帧。当前结果：{formatMs(result.legacy.pipelineMs)} → {formatMs(result.optimized.pipelineMs)}。
              </p>
            </section>

            <section className="border-t-2 border-[var(--accent)] bg-[var(--bg-elev)] px-5 py-4 shadow-[var(--shadow-sm)]">
              <div className="border-b border-[var(--border-strong)] pb-4">
                <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-[var(--fg-subtle)]">02 / Integrity</p>
                <h2 className="mt-1 text-base font-semibold">协议回放门禁</h2>
              </div>
              <div className="divide-y divide-[var(--border)]">
                {[
                  ["任意 TCP 拆包", result.protocol.parserPass, `最小 ${result.protocol.smallestChunkBytes} byte`],
                  ["事件 ID 连续", result.protocol.parserPass, `${result.protocol.decodedEvents} events`],
                  ["断点续传无重无漏", result.protocol.resumePass, `${result.protocol.replayedEvents} replayed`],
                  ["坏帧容错跳过", result.robustness.corruptFramePass, `${result.robustness.droppedFrames} dropped`],
                  ["停滞看门狗检测", result.robustness.watchdogPass, "stall → resume"],
                  ["后台标签页兜底提交", result.robustness.schedulerPass, "rAF + timer"],
                  ["刷新续传持久化", result.robustness.sessionPersistencePass, "cursor + draft"],
                  ["Unicode 码点完整", result.robustness.unicodeBufferPass, "surrogate-safe"],
                  ["流式原文完整", result.robustness.streamingTextIntegrityPass, "transport buffer remains lossless"],
                  ["Markdown 稳定投影", result.robustness.streamingMarkdownProjectionPass, "plain text streams; open syntax waits"],
                  ["重复订阅事件去重", result.robustness.duplicateReplayPass, "shared cursor → exactly once"],
                  ["Artifact 刷新恢复", result.robustness.artifactRestorePass, "persisted tool part → panel"],
                ].map(([label, ok, detail]) => (
                  <div key={String(label)} className="flex items-center gap-3 py-4">
                    <span className={`grid h-6 w-6 place-items-center rounded-full ${ok ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--signal-soft)] text-[var(--signal)]"}`}>
                      <Check className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-xs font-semibold">{String(label)}</span>
                      <span className="mt-0.5 block font-mono text-[9px] text-[var(--fg-subtle)]">{String(detail)}</span>
                    </span>
                    <span className="font-mono text-[9px] font-semibold">{ok ? "PASS" : "FAIL"}</span>
                  </div>
                ))}
              </div>
              <div className={`mt-4 flex items-center gap-2 rounded-lg px-3 py-2 text-[10px] font-semibold ${passed ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]" : "bg-[var(--signal-soft)] text-[var(--signal)]"}`}>
                <ShieldCheck className="h-3.5 w-3.5" />
                {passed ? "SSE 性能与完整性门禁通过" : "存在未通过的 SSE 门禁"}
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
