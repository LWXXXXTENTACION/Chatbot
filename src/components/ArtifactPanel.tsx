"use client";

import { useMemo, useState } from "react";
import { Check, Code2, Copy, Eye, Loader2, X } from "lucide-react";
import { useChatStore } from "@/lib/store";
import { Markdown } from "./Markdown";

export function ArtifactPanel() {
  const artifact = useChatStore((s) => s.artifacts[s.activeId || ""]) || null;
  const closeArtifact = useChatStore((s) => s.closeArtifact);
  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [copied, setCopied] = useState(false);

  const canPreview = artifact?.kind === "html" || artifact?.kind === "svg";
  const effectiveTab = canPreview ? tab : "code";

  const previewDoc = useMemo(() => {
    if (!artifact) return "";
    if (artifact.kind === "svg") {
      return `<!doctype html><html><head><meta charset="utf-8"><style>
        html,body{margin:0;height:100%;display:grid;place-items:center;background:#fff}
        svg{max-width:100%;max-height:100%}
      </style></head><body>${artifact.content}</body></html>`;
    }
    return artifact.content;
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
    <aside className="fade-in-up flex h-full w-[clamp(360px,42vw,640px)] shrink-0 flex-col border-l border-[var(--border)] bg-[var(--bg-elev)]">
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
            sandbox="allow-scripts allow-same-origin"
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
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Eye;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium transition-colors ${
        active
          ? "bg-[var(--bg-elev)] text-[var(--fg)] shadow-[var(--shadow-sm)]"
          : "text-[var(--fg-muted)] hover:text-[var(--fg)]"
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}
