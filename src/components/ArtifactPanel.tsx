"use client";

import { useMemo, useState } from "react";
import { Check, Code2, Copy, Eye, Loader2, X } from "lucide-react";
import { useChatStore } from "@/lib/store";
import { limitArtifactContent, secureArtifactPreview } from "@/lib/artifact-security";
import { Markdown } from "./Markdown";

export function ArtifactPanel() {
  const artifact = useChatStore((s) => s.artifacts[s.activeId || ""]) || null;
  const closeArtifact = useChatStore((s) => s.closeArtifact);
  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [copied, setCopied] = useState(false);

  const canPreview = artifact?.kind === "html" || artifact?.kind === "svg";
  // HTML/SVG 在 tool_call_delta 阶段几乎每次都变化。若不断更新 iframe.srcDoc，
  // iframe 会反复整页加载并闪烁；因此生成中固定显示源码占位，结束后一次性挂载预览。
  const previewReady = canPreview && !artifact?.streaming;
  const effectiveTab = previewReady ? tab : "code";

  const previewDoc = useMemo(() => {
    // secureArtifactPreview 会注入 CSP/安全外壳；iframe 再用 sandbox 隔离工件脚本，
    // 工件内容始终被视为不可信数据，不能直接进入主应用 DOM。
    if (!artifact || artifact.streaming) return "";
    if (artifact.kind === "svg") {
      return secureArtifactPreview(`<style>
        html,body{margin:0;height:100%;display:grid;place-items:center;background:#fff}
        svg{max-width:100%;max-height:100%}
      </style>${limitArtifactContent(artifact.content)}`);
    }
    return secureArtifactPreview(artifact.content);
  }, [artifact]);

  if (!artifact) return null;

  async function copy() {
    if (!artifact) return;
    try {
      await navigator.clipboard.writeText(artifact.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* ignore */
    }
  }

  const codeFence =
    artifact.kind === "markdown"
      ? artifact.content
      : "```" +
        (artifact.language || (artifact.kind === "html" ? "html" : artifact.kind === "svg" ? "xml" : "")) +
        "\n" +
        artifact.content +
        "\n```";

  return (
    <aside className="fade-in-up fixed inset-0 z-40 flex h-full w-full shrink-0 flex-col bg-[var(--bg-elev)] md:relative md:inset-auto md:z-auto md:w-[clamp(360px,42vw,640px)] md:border-l md:border-[var(--border)]">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-semibold tracking-tight text-[var(--fg)]">
            {artifact.title}
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[var(--fg-subtle)]">
            <span className="uppercase tracking-wider">
              {artifact.language || artifact.kind}
            </span>
            {artifact.streaming ? (
              <span className="inline-flex items-center gap-1 text-[var(--accent-strong)]">
                <Loader2 className="h-3 w-3 animate-spin" />
                生成中
              </span>
            ) : null}
          </p>
        </div>

        {canPreview ? (
          <div className="flex items-center rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)] p-0.5 text-[12px]">
            <TabButton
              active={effectiveTab === "preview"}
              onClick={() => setTab("preview")}
              icon={Eye}
              label="预览"
              disabled={!previewReady}
            />
            <TabButton
              active={effectiveTab === "code"}
              onClick={() => setTab("code")}
              icon={Code2}
              label="源码"
            />
          </div>
        ) : null}

        <button
          onClick={copy}
          className="flex h-8 items-center gap-1.5 rounded-lg border border-[var(--border)] px-2.5 text-[12px] font-medium text-[var(--fg-muted)] transition-colors hover:bg-[var(--sidebar-item-hover)] hover:text-[var(--fg)]"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-emerald-500" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={closeArtifact}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--fg-muted)] transition-colors hover:bg-[var(--sidebar-item-hover)] hover:text-[var(--fg)]"
          aria-label="关闭"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Body */}
      <div className="scrollbar-thin min-h-0 flex-1 overflow-auto">
        {effectiveTab === "preview" && canPreview ? (
          <iframe
            title={artifact.title}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            className="h-full w-full border-0 bg-white"
            srcDoc={previewDoc}
          />
        ) : artifact.kind === "markdown" ? (
          <div className="px-5 py-4">
            <Markdown>{artifact.content}</Markdown>
          </div>
        ) : (
          <div className="px-4 py-3">
            <Markdown>{codeFence}</Markdown>
          </div>
        )}
      </div>
    </aside>
  );
}

function TabButton({
  active,
  onClick,
  icon: Icon,
  label,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Eye;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={disabled ? "生成完成后可预览" : undefined}
      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium transition-colors ${
        active
          ? "bg-[var(--bg-elev)] text-[var(--fg)] shadow-[var(--shadow-sm)]"
          : "text-[var(--fg-muted)] hover:text-[var(--fg)]"
      } disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-[var(--fg-muted)]`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}
