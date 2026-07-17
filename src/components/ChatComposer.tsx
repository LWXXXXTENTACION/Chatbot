"use client";

import { memo, useEffect, useRef } from "react";
import { ArrowUp, Globe2, Square, Telescope, type LucideIcon } from "lucide-react";
import type { SearchMode } from "@/lib/types";

interface ChatComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  status: "submitted" | "streaming" | "ready" | "error";
  disabled?: boolean;
  searchMode: SearchMode;
  onSearchModeChange: (mode: SearchMode) => void;
  searchDisabled?: boolean;
  placeholder?: string;
}

const SEARCH_OPTIONS: Array<{
  mode: Exclude<SearchMode, "auto">;
  label: string;
  hint: string;
  icon: LucideIcon;
  activeClass: string;
}> = [
  {
    mode: "web",
    label: "联网搜索",
    hint: "快速搜索网页并附上来源",
    icon: Globe2,
    activeClass: "border-[var(--accent)]/35 bg-[var(--accent-soft)] text-[var(--accent-strong)]",
  },
  {
    mode: "deep",
    label: "深度搜索",
    hint: "多方向检索、整理证据并附上引用",
    icon: Telescope,
    activeClass: "border-[var(--signal)]/35 bg-[var(--signal-soft)] text-[var(--signal)]",
  },
];

function ChatComposerImpl({
  value,
  onChange,
  onSubmit,
  onStop,
  status,
  disabled,
  searchMode,
  onSearchModeChange,
  searchDisabled,
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
      <div className="group relative flex flex-col rounded-[20px] border border-[var(--border)] bg-[var(--bg-elev)] p-2 shadow-[var(--shadow-md)] transition-all focus-within:border-[var(--accent)]/40 focus-within:shadow-[var(--shadow-glow)]">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={placeholder}
          disabled={disabled}
          className="scrollbar-thin max-h-60 min-h-[40px] w-full resize-none bg-transparent px-3 py-2 text-[14.5px] leading-relaxed text-[var(--fg)] outline-none placeholder:text-[var(--fg-subtle)] disabled:opacity-50"
        />
        <div className="flex items-center justify-between gap-2 px-1 pb-0.5 pt-1">
          <div className="flex min-w-0 items-center gap-1.5" role="group" aria-label="搜索模式">
            {SEARCH_OPTIONS.map((option) => {
              const active = searchMode === option.mode;
              const Icon = option.icon;
              return (
                <button
                  key={option.mode}
                  type="button"
                  disabled={busy || disabled || searchDisabled}
                  aria-pressed={active}
                  title={searchDisabled ? "当前模型不支持工具调用" : option.hint}
                  onClick={() => onSearchModeChange(active ? "auto" : option.mode)}
                  className={`focus-ring inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[12px] font-medium transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 ${
                    active
                      ? option.activeClass
                      : "border-transparent text-[var(--fg-muted)] hover:border-[var(--border)] hover:bg-[var(--bg-subtle)] hover:text-[var(--fg)]"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" strokeWidth={2.1} />
                  <span>{option.label}</span>
                  {active ? <span className="h-1 w-1 rounded-full bg-current" /> : null}
                </button>
              );
            })}
          </div>

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
              className="focus-ring flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--accent)] text-white shadow-[var(--shadow-sm)] transition-all hover:bg-[var(--accent-strong)] active:scale-[0.97] disabled:cursor-not-allowed disabled:bg-[var(--bg-subtle)] disabled:text-[var(--fg-subtle)] disabled:shadow-none"
              aria-label="发送"
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.6} />
            </button>
          )}
        </div>
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

export const ChatComposer = memo(ChatComposerImpl);
