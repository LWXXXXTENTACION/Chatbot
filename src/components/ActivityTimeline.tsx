"use client";

import { Brain, Search, FileText, PenLine, RefreshCw } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Activity } from "@/lib/types";

const ACTIVITY_ICONS: Record<Activity["kind"], LucideIcon> = {
  searching: Search,
  retrieved: FileText,
  analyzing: Brain,
  answering: PenLine,
  rewriting: RefreshCw,
};

export function ActivityTimeline({ activities }: { activities: Activity[] }) {
  if (!activities.length) return null;

  return (
    <div className="my-2 rounded-2xl border border-[var(--border)] bg-[var(--bg-elev)]/60 p-3 backdrop-blur">
      <p className="mb-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--fg-subtle)]">
        🔬 研究进度
      </p>
      <div className="space-y-1.5">
        {activities.map((a, i) => {
          const Icon = ACTIVITY_ICONS[a.kind];
          const isLatest = i === activities.length - 1;
          return (
            <div
              key={i}
              className={`flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[12px] transition-all ${
                isLatest ? "bg-[var(--accent-soft)] text-[var(--accent-strong)]" : "text-[var(--fg-muted)]"
              }`}
            >
              <Icon className={`h-3.5 w-3.5 shrink-0 ${isLatest ? "animate-pulse" : ""}`} />
              <span className="leading-tight">{a.message}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
