"use client";

import { useState } from "react";
import { Brain, ChevronDown, Sparkles } from "lucide-react";
import { Markdown } from "./Markdown";
import { ToolInvocation } from "./ToolInvocation";
import { ArtifactCard } from "./ArtifactCard";
import { SourcesFootnotes } from "./SourcesFootnotes";
import { useChatStore } from "@/lib/store";
import type { ChatUIMessage, ToolState } from "@/lib/types";

interface MessageBubbleProps {
  message: ChatUIMessage;
  isStreaming?: boolean;
  conversationId?: string;
}

type AnyPart = ChatUIMessage["parts"][number];

export function MessageBubble({ message, isStreaming, conversationId }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  const messageSources = useChatStore((s) => s.messageSources[message.id]);

  if (isUser) {
    const text = message.parts
      .filter((p) => p.type === "text")
      .map((p) => (p as { text: string }).text)
      .join("");
    return (
      <div className="fade-in-up flex justify-end gap-3" data-role="user">
        <div className="flex max-w-[78%] flex-col items-end">
          <div className="rounded-2xl rounded-tr-md bg-[var(--user-bubble)] px-4 py-2.5 text-[var(--user-bubble-fg)] shadow-[var(--shadow-sm)]">
            <p className="whitespace-pre-wrap text-[14.5px] leading-relaxed">
              {text}
            </p>
          </div>
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--bg-elev)] text-[11px] font-semibold tracking-tight text-[var(--fg)]">
          我
        </div>
      </div>
    );
  }

  const hasRenderable = message.parts.some(
    (p) =>
      p.type === "text" ||
      p.type === "reasoning" ||
      p.type.startsWith("tool-"),
  );

  return (
    <div className="fade-in-up flex justify-start gap-3" data-role="assistant">
      <div className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white shadow-[0_4px_14px_-4px_rgba(99,102,241,0.55)]">
        <Sparkles className="h-4 w-4" strokeWidth={2.4} />
      </div>

      <div className="flex min-w-0 max-w-[82%] flex-1 flex-col gap-2.5">
        {message.parts.map((part, i) => (
          <PartView
            key={`${message.id}-${i}`}
            part={part}
            streaming={isStreaming}
            conversationId={conversationId}
            sources={messageSources}
          />
        ))}
        <SourcesFootnotes sources={messageSources || []} />

        {isAssistant && isStreaming && !hasRenderable ? (
          <TypingIndicator />
        ) : null}
      </div>
    </div>
  );
}

function PartView({
  part,
  streaming,
  conversationId,
  sources,
}: {
  part: AnyPart;
  streaming?: boolean;
  conversationId?: string;
  sources?: import("@/lib/types").Source[];
}) {
  if (part.type === "text") {
    const text = (part as { text: string }).text;
    if (!text) return null;
    return <Markdown sources={sources}>{text}</Markdown>;
  }

  if (part.type === "reasoning") {
    const text = (part as { text: string }).text;
    const state = (part as { state?: string }).state;
    if (!text) return null;
    return (
      <ReasoningBlock text={text} streaming={streaming && state === "streaming"} />
    );
  }

  if (part.type === "tool-create_artifact") {
    const p = part as {
      toolCallId: string;
      state: ToolState;
      input?: unknown;
    };
    return (
      <ArtifactCard
        toolCallId={p.toolCallId}
        state={p.state}
        input={p.input as never}
        conversationId={conversationId!}
      />
    );
  }

  if (part.type.startsWith("tool-")) {
    const p = part as {
      type: string;
      toolCallId: string;
      state: ToolState;
      input?: unknown;
      output?: unknown;
      errorText?: string;
    };
    return <ToolInvocation part={p as never} />;
  }

  return null;
}

function ReasoningBlock({
  text,
  streaming,
}: {
  text: string;
  streaming?: boolean;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="w-full overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)]/70 text-[13px] shadow-[var(--shadow-sm)] backdrop-blur">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left"
      >
        <div className="flex h-5 w-5 items-center justify-center rounded-md bg-[var(--accent-soft)] text-[var(--accent-strong)]">
          <Brain className="h-3 w-3" strokeWidth={2.5} />
        </div>
        <span
          className={`font-medium ${
            streaming ? "shimmer-text" : "text-[var(--fg)]"
          }`}
        >
          {streaming ? "正在深度思考…" : "思考过程"}
        </span>
        <ChevronDown
          className={`ml-auto h-4 w-4 text-[var(--fg-subtle)] transition-transform ${
            open ? "" : "-rotate-90"
          }`}
        />
      </button>
      {open ? (
        <div className="border-t border-[var(--border)] px-3.5 py-3 text-[13px] leading-relaxed text-[var(--fg-muted)]">
          <Markdown>{text}</Markdown>
        </div>
      ) : null}
    </div>
  );
}

function TypingIndicator() {
  return (
    <span className="inline-flex items-center py-2 text-[var(--fg-subtle)]">
      <span className="typing-dot" />
      <span className="typing-dot" />
      <span className="typing-dot" />
    </span>
  );
}
