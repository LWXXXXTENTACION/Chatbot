"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { MessageSquarePlus } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { ChatView } from "@/components/ChatView";
import { ArtifactPanel } from "@/components/ArtifactPanel";
import { AuthGuard } from "@/components/AuthGuard";
import { useChatStore } from "@/lib/store";

/** Maximum number of ChatViews to keep mounted simultaneously. */
const KEEP_ALIVE_MAX = 5;

export default function HomePage() {
  const conversations = useChatStore((s) => s.conversations);
  const activeId = useChatStore((s) => s.activeId);
  const artifactOpen = useChatStore((s) => s.artifactOpen);
  const streamingIds = useChatStore((s) => s.streamingIds);

  // Auto-load messages for the active conversation on mount / switch.
  // loadConversations() only fetches the list; individual messages are
  // fetched lazily. Without this effect, the initial active conversation
  // would show an empty ChatView even though messages exist in the DB.
  const messagesLoadedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!activeId) return;
    // Check the store directly (not the `conversations` state from this
    // render) to avoid a dependency on the conversations array — which
    // changes on every message sync and would re-trigger this effect.
    const store = useChatStore.getState();
    const conv = store.conversations.find((c) => c.id === activeId);
    if (!conv || conv.messages.length > 0) return;
    if (messagesLoadedRef.current.has(activeId)) return;
    messagesLoadedRef.current.add(activeId);
    store.loadMessages(activeId);
  }, [activeId]);

  // Keep-alive: maintain mounted ChatViews for the active conversation,
  // any conversation with an in-flight stream, and the most recent
  // conversations (up to KEEP_ALIVE_MAX total). Non-visible views are
  // hidden with CSS `display:none` so their useChatStream hooks stay
  // alive and continue processing SSE events in the background.
  const keepAliveIds = useMemo(() => {
    const ids = new Set<string>();

    // Active conversation first
    if (activeId) ids.add(activeId);

    // All streaming conversations (may include non-active ones)
    if (streamingIds) {
      streamingIds.forEach((id) => ids.add(id));
    }

    // Fill remaining slots with most recent conversations
    if (ids.size < KEEP_ALIVE_MAX) {
      const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
      for (const c of sorted) {
        if (ids.size >= KEEP_ALIVE_MAX) break;
        ids.add(c.id);
      }
    }

    return ids;
  }, [activeId, streamingIds, conversations]);

  const keepAliveConversations = conversations.filter((c) =>
    keepAliveIds.has(c.id),
  );

  return (
    <AuthGuard>
      <main className="app-bg flex h-full">
        <Sidebar />
        <div className="flex h-full min-w-0 flex-1">
          {keepAliveConversations.length > 0 ? (
            keepAliveConversations.map((conv) => {
              const isActive = conv.id === activeId;
              return (
                <div
                  key={conv.id}
                  className={
                    isActive
                      ? "flex h-full min-w-0 flex-1"
                      : "hidden"
                  }
                  aria-hidden={!isActive}
                >
                  <ChatView conversation={conv} />
                </div>
              );
            })
          ) : (
            <NoConversationState />
          )}
          {artifactOpen ? <ArtifactPanel /> : null}
        </div>
      </main>
    </AuthGuard>
  );
}

function NoConversationState() {
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  async function handleCreate() {
    setCreating(true);
    setError("");
    const created = await useChatStore.getState().createConversation();
    if (!created) setError("暂时无法新建，请确认后端服务已启动");
    setCreating(false);
  }

  return (
    <div className="flex h-full flex-1 items-center justify-center px-6">
      <div className="flex max-w-sm flex-col items-center text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-[var(--border-strong)] bg-[var(--bg-elev)] text-[var(--accent-strong)] shadow-[var(--shadow-sm)]">
          <MessageSquarePlus className="h-5 w-5" />
        </div>
        <h1 className="mt-5 text-xl font-semibold tracking-tight text-[var(--fg)]">
          对话列表为空
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-[var(--fg-muted)]">
          创建一个由后端保存的新对话，开始记录你的问题和回答。
        </p>
        <button
          type="button"
          onClick={handleCreate}
          disabled={creating}
          className="focus-ring mt-5 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-[var(--accent-strong)] disabled:opacity-50"
        >
          {creating ? "正在创建…" : "新建对话"}
        </button>
        {error ? <p className="mt-3 text-xs text-red-600 dark:text-red-400">{error}</p> : null}
      </div>
    </div>
  );
}
