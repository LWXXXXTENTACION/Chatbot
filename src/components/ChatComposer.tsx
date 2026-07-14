"use client";

import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";

interface ChatComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  status: "submitted" | "streaming" | "ready" | "error";
  disabled?: boolean;
  placeholder?: string;
}

export function ChatComposer({
  value,
  onChange,
  onSubmit,
  onStop,
  status,
  disabled,
  placeholder = "给 DeepSeek 发送消息…",
}: ChatComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const busy = status === "streaming" || status === "submitted";

  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "0px";
    const next = Math.min(ta.scrollHeight, 240);
    ta.style.height = `${next}px`;
  }, [value]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!busy && value.trim() && !disabled) onSubmit();
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy && value.trim() && !disabled) onSubmit();
      }}
      className="relative w-full"
    >
      <div className="group relative flex items-end gap-2 rounded-[20px] border border-[var(--border)] bg-[var(--bg-elev)] p-2 shadow-[var(--shadow-md)] transition-all focus-within:border-[var(--accent)]/40 focus-within:shadow-[var(--shadow-glow)]">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={placeholder}
          disabled={disabled}
          className="scrollbar-thin max-h-60 min-h-[40px] flex-1 resize-none bg-transparent px-3 py-2 text-[14.5px] leading-relaxed text-[var(--fg)] outline-none placeholder:text-[var(--fg-subtle)] disabled:opacity-50"
        />
        {busy && onStop ? (
          <button
            type="button"
            onClick={onStop}
            className="focus-ring flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--fg)] text-[var(--bg)] shadow-[var(--shadow-sm)] transition-transform hover:scale-[1.03] active:scale-[0.97]"
            aria-label="停止生成"
          >
            <Square className="h-3.5 w-3.5" fill="currentColor" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={busy || !value.trim() || disabled}
            className="focus-ring flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white shadow-[0_6px_18px_-4px_rgba(99,102,241,0.5)] transition-all hover:shadow-[0_8px_22px_-4px_rgba(99,102,241,0.6)] active:scale-[0.97] disabled:cursor-not-allowed disabled:from-[var(--bg-subtle)] disabled:to-[var(--bg-subtle)] disabled:text-[var(--fg-subtle)] disabled:shadow-none"
            aria-label="发送"
          >
            <ArrowUp className="h-4 w-4" strokeWidth={2.6} />
          </button>
        )}
      </div>
      <p className="mt-2 px-1 text-center text-[11px] text-[var(--fg-subtle)]">
        <kbd className="rounded border border-[var(--border)] bg-[var(--bg-elev)] px-1.5 py-[1px] font-mono text-[10px]">
          Enter
        </kbd>{" "}
        发送 ·{" "}
        <kbd className="rounded border border-[var(--border)] bg-[var(--bg-elev)] px-1.5 py-[1px] font-mono text-[10px]">
          Shift + Enter
        </kbd>{" "}
        换行
      </p>
    </form>
  );
}
