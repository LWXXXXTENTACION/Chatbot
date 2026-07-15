"use client";

import { useEffect, useRef } from "react";
import {
  Code2,
  FileText,
  Globe,
  Image as ImageIcon,
  Loader2,
  SquareArrowOutUpRight,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useChatStore } from "@/lib/store";
import type { Artifact, ArtifactKind } from "@/lib/types";
import { extractArtifactFields } from "@/lib/partial-json";

interface CreateArtifactInput {
  title?: string;
  kind?: ArtifactKind;
  language?: string;
  content?: string;
}

/** Resolve input that may be a raw JSON string (during streaming) or a parsed object. */
function resolveInput(
  input: CreateArtifactInput | string | undefined,
): CreateArtifactInput {
  if (!input) return {};
  if (typeof input === "string") {
    try {
      return JSON.parse(input) as CreateArtifactInput;
    } catch {
      /* fall through to partial extraction */
    }
    const partial = extractArtifactFields(input);
    return {
      title: partial.title,
      kind: partial.kind as ArtifactKind | undefined,
      language: partial.language,
      content: partial.content,
    };
  }
  return input;
}

interface ArtifactCardProps {
  toolCallId: string;
  state:
    | "input-streaming"
    | "input-available"
    | "approval-requested"
    | "output-available"
    | "output-error";
  input?: CreateArtifactInput;
  conversationId: string;
}

const KIND_META: Record<ArtifactKind, { label: string; icon: LucideIcon }> = {
  code: { label: "代码", icon: Code2 },
  html: { label: "网页", icon: Globe },
  markdown: { label: "文档", icon: FileText },
  svg: { label: "图形", icon: ImageIcon },
};

export function ArtifactCard({ toolCallId, state, input, conversationId }: ArtifactCardProps) {
  const openArtifact = useChatStore((s) => s.openArtifact);
  const openedRef = useRef(false);

  // Resolve input: may be a raw JSON string during streaming, or parsed object.
  const resolved = resolveInput(input);

  const kind: ArtifactKind = resolved.kind ?? "code";
  const title = resolved.title ?? "未命名工件";
  const content = resolved.content ?? "";
  const streaming = state === "input-streaming";
  const meta = KIND_META[kind] ?? KIND_META.code;
  const Icon = meta.icon;

  const artifact: Artifact = {
    id: toolCallId,
    title,
    kind,
    language: resolved.language,
    content,
    streaming,
  };

  // Auto-open on first appearance. After that, do NOT call updateArtifact here.
  // The hook's tool_call_delta handler already syncs the panel during streaming
  // via store.updateArtifact. Calling it again from completed cards causes them
  // to fight over the panel, creating flickering.
  useEffect(() => {
    if (!input) return;
    if (!openedRef.current) {
      openArtifact(conversationId, artifact);
      openedRef.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, title, kind, streaming]);

  return (
    <button
      onClick={() => openArtifact(conversationId, artifact)}
      className="group flex w-full items-center gap-3 overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)] px-3.5 py-3 text-left shadow-[var(--shadow-sm)] transition-all hover:-translate-y-[1px] hover:border-[var(--border-strong)] hover:shadow-[var(--shadow-md)]"
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--border-strong)] bg-[var(--accent-soft)] text-[var(--accent-strong)]">
        <Icon className="h-5 w-5" strokeWidth={2.1} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13.5px] font-semibold tracking-tight text-[var(--fg)]">
          {title}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-[var(--fg-muted)]">
          <span className="rounded bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[10px] font-medium">
            {meta.label}
            {input?.language ? ` · ${input.language}` : ""}
          </span>
          {streaming ? (
            <span className="inline-flex items-center gap-1 text-[var(--accent-strong)]">
              <Loader2 className="h-3 w-3 animate-spin" />
              生成中…
            </span>
          ) : (
            <span>点击查看</span>
          )}
        </p>
      </div>
      <SquareArrowOutUpRight className="h-4 w-4 shrink-0 text-[var(--fg-subtle)] transition-colors group-hover:text-[var(--accent)]" />
    </button>
  );
}
