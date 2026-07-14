"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Sparkles } from "lucide-react";
import {
  DEEPSEEK_MODELS,
  type DeepSeekModelId,
  getModel,
} from "@/lib/models";

interface ModelSelectorProps {
  value: DeepSeekModelId;
  onChange: (value: DeepSeekModelId) => void;
  disabled?: boolean;
}

export function ModelSelector({ value, onChange, disabled }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const current = getModel(value);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="focus-ring group flex items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--bg-elev)] px-3 py-1.5 text-[13px] font-medium text-[var(--fg)] shadow-[var(--shadow-sm)] transition-all hover:border-[var(--border-strong)] hover:shadow-[var(--shadow-md)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Sparkles className="h-3.5 w-3.5 text-[var(--accent)]" />
        <span className="font-semibold tracking-tight">{current.name}</span>
        {current.badge ? (
          <span className="rounded-md bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-strong)]">
            {current.badge}
          </span>
        ) : null}
        <ChevronDown
          className={`h-3.5 w-3.5 text-[var(--fg-subtle)] transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open ? (
        <div className="absolute left-0 top-full z-30 mt-2 w-[320px] origin-top-left animate-in rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)] p-1.5 shadow-[var(--shadow-lg)] fade-in-up">
          <div className="px-3 pt-2 pb-1">
            <p className="text-[10.5px] font-medium uppercase tracking-[0.12em] text-[var(--fg-subtle)]">
              选择模型
            </p>
          </div>
          {DEEPSEEK_MODELS.map((model) => {
            const selected = model.id === value;
            return (
              <button
                key={model.id}
                type="button"
                onClick={() => {
                  onChange(model.id);
                  setOpen(false);
                }}
                className={`relative flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition-colors ${
                  selected
                    ? "bg-[var(--accent-soft)]"
                    : "hover:bg-[var(--sidebar-item-hover)]"
                }`}
              >
                <div className="flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-semibold tracking-tight text-[var(--fg)]">
                      {model.name}
                    </span>
                    {model.badge ? (
                      <span
                        className={`rounded-md px-1.5 py-0.5 text-[9.5px] font-medium ${
                          selected
                            ? "bg-[var(--bg-elev)] text-[var(--accent-strong)]"
                            : "bg-[var(--bg-subtle)] text-[var(--fg-muted)]"
                        }`}
                      >
                        {model.badge}
                      </span>
                    ) : null}
                    {model.deprecated ? (
                      <span className="rounded-md bg-amber-500/10 px-1.5 py-0.5 text-[9.5px] font-medium text-amber-600 dark:text-amber-400">
                        {model.deprecated} 停用
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-[var(--fg-muted)]">
                    {model.description}
                  </p>
                </div>
                {selected ? (
                  <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--accent)] text-white shadow-[0_2px_6px_-2px_rgba(99,102,241,0.6)]">
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </div>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
