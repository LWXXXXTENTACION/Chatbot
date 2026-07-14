"use client";

import { useState } from "react";
import { ExternalLink } from "lucide-react";
import type { Source } from "@/lib/types";

interface SourcesFootnotesProps {
  sources: Source[];
}

export function SourcesFootnotes({ sources }: SourcesFootnotesProps) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  if (!sources.length) return null;

  return (
    <div className="sources-footnotes">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--fg-subtle)]">
        参考来源
      </div>
      <ol className="space-y-2">
        {sources.map((source, i) => (
          <li key={i} value={i + 1} className="text-[13px]">
            <button
              type="button"
              onClick={() =>
                setExpanded((prev) => ({ ...prev, [i]: !prev[i] }))
              }
              className="group flex w-full items-start gap-2 text-left"
            >
              <span className="citation-badge shrink-0">
                <span className="inline-flex items-center justify-center rounded-sm bg-[var(--accent-soft)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--accent)]">
                  {i + 1}
                </span>
              </span>
              <span className="min-w-0 flex-1">
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-[var(--fg)] hover:text-[var(--accent)] transition-colors"
                >
                  {source.title}
                </a>
                <span className="ml-1.5 inline-flex items-center text-[var(--fg-subtle)] opacity-0 group-hover:opacity-100 transition-opacity">
                  <ExternalLink className="h-3 w-3" />
                </span>
                {expanded[i] && (
                  <p className="mt-1 text-[12px] text-[var(--fg-muted)] leading-relaxed">
                    {source.content}
                  </p>
                )}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
