"use client";

import { memo, useEffect, useRef, useState } from "react";
import { MessageSquarePlus, Trash2, MessageSquareText, LogOut } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useChatStore } from "@/lib/store";
import { useAuth } from "@/providers/AuthProvider";
import { ThemeToggle } from "@/components/ThemeToggle";

interface SidebarProps {
  mobileOpen?: boolean;
  onClose?: () => void;
}

export function Sidebar({ mobileOpen = false, onClose }: SidebarProps) {
  const conversationIds = useChatStore(
    useShallow((state) => state.conversations.map((conversation) => conversation.id)),
  );
  const activeId = useChatStore((s) => s.activeId);
  const createConversation = useChatStore((s) => s.createConversation);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const loadMessages = useChatStore((s) => s.loadMessages);
  const { user, logout } = useAuth();
  const [createError, setCreateError] = useState("");

  // loadConversations() is called once in AuthProvider after auth
  // — no need to call it here anymore.
  // Track in-flight loads to prevent concurrent duplicate requests.
  const loadingRef = useRef<Set<string>>(new Set());

  function handleSelect(id: string) {
    selectConversation(id);
    onClose?.();
    // Skip load if already loading or messages already cached
    const conv = useChatStore.getState().conversations.find((c) => c.id === id);
    if (conv && conv.messages.length > 0) return;
    if (loadingRef.current.has(id)) return;
    loadingRef.current.add(id);
    loadMessages(id).finally(() => loadingRef.current.delete(id));
  }

  async function handleNew() {
    setCreateError("");
    const created = await createConversation();
    if (!created) {
      setCreateError("新建失败，请检查后端连接");
    } else {
      onClose?.();
    }
  }

  async function handleDelete(id: string) {
    const conv = useChatStore.getState().conversations.find((c) => c.id === id);
    if (conv && confirm(`删除对话「${conv.title || "新对话"}」？`)) {
      await deleteConversation(id);
    }
  }

  const userInitial = user?.username?.charAt(0).toUpperCase() || "U";

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "n") return;
      event.preventDefault();
      void handleNew();
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
    // createConversation and onClose are stable store/parent actions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createConversation, onClose]);

  return (
    <aside
      aria-label="对话导航"
      className={`fixed inset-y-0 left-0 z-50 flex h-full w-[min(86vw,320px)] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--sidebar)] shadow-[var(--shadow-lg)] transition-transform duration-200 md:relative md:z-auto md:w-[280px] md:translate-x-0 md:shadow-none ${
        mobileOpen ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 pt-5 pb-4">
        <div className="relative flex h-8 w-8 items-center justify-center rounded-md bg-[var(--fg)] text-[var(--bg-elev)] shadow-[var(--shadow-sm)]">
          <MessageSquareText className="h-4 w-4" strokeWidth={2.2} />
        </div>
        <div className="flex flex-col">
          <span className="text-[13.5px] font-semibold tracking-tight text-[var(--fg)]">
            DeepSeek Chat
          </span>
          <span className="text-[10.5px] uppercase tracking-[0.12em] text-[var(--fg-subtle)]">
            Studio
          </span>
        </div>
      </div>

      <div className="px-3 pb-3">
        <button
          onClick={handleNew}
          className="focus-ring group flex w-full items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--bg-elev)] px-3 py-2 text-[13px] font-medium text-[var(--fg)] shadow-[var(--shadow-sm)] transition-all hover:border-[var(--border-strong)] hover:shadow-[var(--shadow-md)]"
        >
          <MessageSquarePlus className="h-4 w-4 text-[var(--fg-muted)] transition-colors group-hover:text-[var(--accent)]" />
          <span>新建对话</span>
          <span className="ml-auto rounded border border-[var(--border)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--fg-subtle)]">
            ⌘N
          </span>
        </button>
        {createError ? (
          <p className="mt-2 px-1 text-[11px] text-red-600 dark:text-red-400">
            {createError}
          </p>
        ) : null}
      </div>

      <div className="px-4 pt-2 pb-1.5">
        <p className="text-[10.5px] font-medium uppercase tracking-[0.12em] text-[var(--fg-subtle)]">
          最近对话
        </p>
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto px-2 pb-3">
        {conversationIds.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-[var(--fg-subtle)]">
            还没有对话，点击上方按钮创建
          </p>
        ) : (
          <ul className="space-y-0.5">
            {conversationIds.map((conversationId) => (
              <ConversationRow
                key={conversationId}
                conversationId={conversationId}
                active={conversationId === activeId}
                onSelect={handleSelect}
                onDelete={handleDelete}
              />
            ))}
          </ul>
        )}
      </div>

      {/* User section */}
      <div className="border-t border-[var(--border)] px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--border-strong)] bg-[var(--accent-soft)] text-xs font-bold text-[var(--accent-strong)]">
            {userInitial}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[12.5px] font-medium text-[var(--fg)]">
              {user?.username || "用户"}
            </p>
          </div>
          <ThemeToggle />
          <button
            onClick={logout}
            className="rounded-lg p-1.5 text-[var(--fg-muted)] transition-colors hover:bg-[var(--bg-subtle)] hover:text-red-500"
            title="退出登录"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}

const ConversationRow = memo(function ConversationRow({
  conversationId,
  active,
  onSelect,
  onDelete,
}: {
  conversationId: string;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const title = useChatStore((state) =>
    state.conversations.find((conversation) => conversation.id === conversationId)?.title,
  );
  const label = title || "新对话";

  return (
    <li>
      <div
        className={`group flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] transition-colors ${
          active
            ? "bg-[var(--sidebar-item-active)] text-[var(--fg)]"
            : "text-[var(--fg-muted)] hover:bg-[var(--sidebar-item-hover)] hover:text-[var(--fg)]"
        }`}
      >
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            active
              ? "bg-[var(--accent)] shadow-[0_0_0_3px_var(--accent-soft)]"
              : "bg-transparent"
          }`}
        />
        <button
          onClick={() => onSelect(conversationId)}
          className="flex-1 truncate text-left"
          title={label}
        >
          {label}
        </button>
        <button
          onClick={(event) => {
            event.stopPropagation();
            onDelete(conversationId);
          }}
          className="rounded p-1 text-[var(--fg-subtle)] opacity-0 transition-all hover:bg-[var(--bg-elev)] hover:text-red-500 focus-visible:opacity-100 group-hover:opacity-100"
          aria-label={`删除对话：${label}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </li>
  );
});
