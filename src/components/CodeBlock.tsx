"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

interface CodeBlockProps {
  language?: string;
  raw: string;
  children: ReactNode;
}

export function CodeBlock({ language, raw, children }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(raw);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div className="group/code relative my-3 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] shadow-[var(--shadow-sm)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] bg-[var(--bg-elev)]/50 px-3.5 py-1.5">
        <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-[var(--fg-subtle)]">
          {language || "code"}
        </span>
        <button
          onClick={copy}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--fg-muted)] transition-colors hover:bg-[var(--sidebar-item-hover)] hover:text-[var(--fg)]"
          aria-label="复制代码"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-emerald-500" />
              已复制
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              复制
            </>
          )}
        </button>
      </div>
      <pre className="scrollbar-thin !my-0 overflow-x-auto !rounded-none !border-0 !shadow-none">
        {children}
      </pre>
    </div>
  );
}
