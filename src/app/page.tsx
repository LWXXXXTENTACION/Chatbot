"use client";

import { useEffect, useMemo, useRef } from "react";
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
            <div className="flex h-full flex-1 items-center justify-center text-sm text-[var(--fg-muted)]">
              暂无对话，请点击侧边栏新建
            </div>
          )}
          {artifactOpen ? <ArtifactPanel /> : null}
        </div>
      </main>
    </AuthGuard>
  );
}
