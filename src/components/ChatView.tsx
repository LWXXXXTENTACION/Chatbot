"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Code2,
  Compass,
  Lightbulb,
  PencilLine,
  Sparkles,
} from "lucide-react";
import { ModelSelector } from "./ModelSelector";
import { MessageBubble } from "./MessageBubble";
import { ChatComposer } from "./ChatComposer";
import { ActivityTimeline } from "./ActivityTimeline";
import { modelSupportsTools, type DeepSeekModelId } from "@/lib/models";
import { useChatStore } from "@/lib/store";
import type { Conversation, SearchMode } from "@/lib/types";
import { useChatStream } from "@/hooks/useChatStream";

interface ChatViewProps {
  conversation: Conversation;
}

const SUGGESTIONS = [
  {
    icon: Lightbulb,
    title: "解释一个概念",
    prompt: "用通俗易懂的方式给我解释一下 Transformer 架构的核心思想",
  },
  {
    icon: Code2,
    title: "帮我写代码",
    prompt: "用 TypeScript 写一个带优先级的最小堆实现，并给出复杂度说明",
  },
  {
    icon: Compass,
    title: "规划行程",
    prompt: "帮我策划一个 3 天 2 晚的京都自由行，包含交通和必去景点",
  },
  {
    icon: PencilLine,
    title: "润色文字",
    prompt: "帮我把下面这段话改得更有专业感：\n",
  },
];

export function ChatView({ conversation }: ChatViewProps) {
  const setStoreMessages = useChatStore((s) => s.setMessages);
  const setStoreTitle = useChatStore((s) => s.setTitle);
  const setStoreModel = useChatStore((s) => s.setModel);
  const isStoreStreaming = useChatStore((s) => s.isStreaming(conversation.id));

  const [input, setInput] = useState("");
  const [model, setModel] = useState<DeepSeekModelId>(conversation.model);
  const [searchMode, setSearchMode] = useState<SearchMode>("auto");
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { messages, sendMessage, status, error, stop, activities } =
    useChatStream({
      conversationId: conversation.id,
      initialMessages: conversation.messages,
    });

  // Ref to always have the latest messages, used in cleanup
  const messagesSnapRef = useRef(messages);
  messagesSnapRef.current = messages;

  useEffect(() => {
    setModel(conversation.model);
  }, [conversation.id, conversation.model]);

  useEffect(() => {
    setSearchMode("auto");
  }, [conversation.id]);

  // Persist latest messages to store on unmount so no data is lost
  // when switching conversations mid-stream.
  useEffect(() => {
    return () => {
      setStoreMessages(conversation.id, messagesSnapRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation.id, setStoreMessages]);

  useEffect(() => {
    setStoreMessages(conversation.id, messages);
  }, [messages, conversation.id, setStoreMessages]);

  useEffect(() => {
    if (conversation.title && conversation.title !== "新对话") return;
    const firstUser = messages.find((m) => m.role === "user");
    if (!firstUser) return;
    const text = firstUser.parts
      .filter((p) => p.type === "text")
      .map((p) => (p as { text: string }).text)
      .join("")
      .trim();
    if (text) {
      setStoreTitle(conversation.id, text.slice(0, 40));
    }
  }, [messages, conversation.id, conversation.title, setStoreTitle]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  const isEmpty = messages.length === 0;
  // Show "busy" when either hook is streaming or store says this conversation is streaming
  const busy = status === "streaming" || status === "submitted" || isStoreStreaming;

  function handleSend(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || busy) return;
    sendMessage({ text }, { body: { model, searchMode } });
    setInput("");
  }

  function handleModelChange(next: DeepSeekModelId) {
    setModel(next);
    if (!modelSupportsTools(next)) {
      setSearchMode("auto");
    }
    setStoreModel(conversation.id, next);
  }

  return (
    <section className="flex h-full flex-1 flex-col">
      {/* Header */}
      <header className="glass z-10 flex items-center justify-between border-b border-[var(--border)] px-5 py-3">
        <div className="flex items-center gap-3">
          <ModelSelector
            value={model}
            onChange={handleModelChange}
            disabled={busy}
          />
        </div>
        <div className="flex items-center gap-2 text-[11.5px]">
          <span
            className={`relative h-1.5 w-1.5 rounded-full ${
              status === "error"
                ? "bg-red-500"
                : busy
                ? "bg-amber-400"
                : "bg-emerald-500"
            }`}
          >
            {busy ? (
              <span className="absolute inset-0 -m-0.5 animate-ping rounded-full bg-amber-400/60" />
            ) : null}
          </span>
          <span className="font-medium text-[var(--fg-muted)]">
            {busy ? "生成中" : status === "error" ? "出错了" : "就绪"}
          </span>
        </div>
      </header>

      {/* Messages */}
      <div ref={scrollRef} className="scrollbar-thin flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-7 px-5 pt-8 pb-10">
          {isEmpty ? (
            <EmptyState
              onPick={(s) => {
                setInput(s);
                handleSend(s);
              }}
            />
          ) : (
            messages.map((message, idx) => (
              <MessageBubble
                key={message.id}
                message={message}
                isStreaming={busy && idx === messages.length - 1}
                conversationId={conversation.id}
              />
            ))
          )}

          {error ? (
            <div className="fade-in-up flex items-start gap-2.5 rounded-xl border border-red-200/70 bg-red-50/70 px-4 py-3 text-[13px] text-red-700 backdrop-blur dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">请求失败</p>
                <p className="mt-0.5 text-[12.5px] opacity-80">{error.message}</p>
              </div>
            </div>
          ) : null}

          <ActivityTimeline activities={activities} />

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Composer */}
      <div className="border-t border-[var(--border)] bg-[var(--bg)]/60 px-5 py-4 backdrop-blur">
        <div className="mx-auto w-full max-w-3xl">
          <ChatComposer
            value={input}
            onChange={setInput}
            onSubmit={() => handleSend()}
            onStop={stop}
            status={status}
            searchMode={searchMode}
            onSearchModeChange={setSearchMode}
            searchDisabled={!modelSupportsTools(model)}
          />
        </div>
      </div>
    </section>
  );
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="relative flex flex-1 flex-col items-center justify-center pt-10 pb-6 text-center">
      {/* Animated orbs */}
      <div
        className="orb"
        style={{
          width: 220,
          height: 220,
          top: -40,
          left: "calc(50% - 200px)",
          background:
            "radial-gradient(closest-side, var(--orb-a), transparent)",
        }}
      />
      <div
        className="orb"
        style={{
          width: 200,
          height: 200,
          top: 0,
          left: "calc(50% + 20px)",
          animationDelay: "-2s",
          background:
            "radial-gradient(closest-side, var(--orb-b), transparent)",
        }}
      />

      <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white shadow-[0_12px_36px_-8px_rgba(99,102,241,0.55)]">
        <Sparkles className="h-6 w-6" strokeWidth={2.2} />
      </div>

      <h1 className="mt-6 bg-gradient-to-br from-[var(--fg)] to-[var(--fg-muted)] bg-clip-text text-[28px] font-semibold tracking-tight text-transparent">
        有什么可以帮你的吗？
      </h1>
      <p className="mt-2 max-w-md text-[13.5px] text-[var(--fg-muted)]">
        选择一个 DeepSeek 模型，开始对话。你可以在顶部随时切换模型。
      </p>

      <div className="mt-10 grid w-full max-w-2xl grid-cols-1 gap-2.5 sm:grid-cols-2">
        {SUGGESTIONS.map(({ icon: Icon, title, prompt }) => (
          <button
            key={title}
            onClick={() => onPick(prompt)}
            className="focus-ring group relative flex items-start gap-3 overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)] px-4 py-3.5 text-left shadow-[var(--shadow-sm)] transition-all hover:-translate-y-[1px] hover:border-[var(--border-strong)] hover:shadow-[var(--shadow-md)]"
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent-strong)] transition-colors group-hover:bg-[var(--accent)] group-hover:text-white">
              <Icon className="h-4 w-4" strokeWidth={2.2} />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-semibold tracking-tight text-[var(--fg)]">
                {title}
              </p>
              <p className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-[var(--fg-muted)]">
                {prompt}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
