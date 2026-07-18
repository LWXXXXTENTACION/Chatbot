import { create } from "zustand";
import { DEFAULT_MODEL, type DeepSeekModelId } from "./models";
import type { Artifact, ChatUIMessage, Conversation, Source } from "./types";
import { api } from "./api";
import { latestArtifactFromMessages } from "./artifacts";

/**
 * 前端共享状态边界。
 *
 * 服务端数据库是对话消息的事实源；Zustand 只保存当前页面需要的缓存、侧边栏状态
 * 和 AbortController。Artifact 与流状态都按 conversationId 隔离，切换对话时不会
 * 把上一条流或工件串到新对话。高频 token 不直接写 Store，而由 useChatStream 的
 * ref + rAF 缓冲合帧后再同步，避免扩大重渲染范围。
 */

// ---- 状态与动作接口 ----

interface ChatState {
  // Conversation state (transient, API-driven)
  conversations: Conversation[];
  activeId: string | null;
  hydrated: boolean;
  isLoading: boolean;

  // 每个对话独立保存最新 Artifact；artifactOpen 只控制当前活动侧栏是否可见。
  artifacts: Record<string, Artifact>;
  artifactOpen: boolean;

  // Citation sources — per-message to prevent cross-talk
  messageSources: Record<string, Source[]>;
  setMessageSources: (messageId: string, sources: Source[]) => void;

  // 流控制器放在组件外，使切换对话/组件卸载后仍能准确取消旧订阅。
  streamingIds: Set<string>;
  markStreaming: (id: string) => void;
  markStreamDone: (id: string) => void;
  isStreaming: (id: string) => boolean;
  _streamAborts: Record<string, AbortController | null>;
  abortStream: (id: string) => void;
  setStreamAbort: (id: string, ctrl: AbortController) => void;

  // Selectors
  getActive: () => Conversation | null;

  // API-driven conversation actions
  loadConversations: () => Promise<void>;
  createConversation: () => Promise<Conversation | null>;
  selectConversation: (id: string) => void;
  deleteConversation: (id: string) => Promise<void>;
  loadMessages: (id: string) => Promise<ChatUIMessage[]>;

  // Local state updates (for ChatView sync)
  setMessages: (id: string, messages: ChatUIMessage[]) => void;
  setTitle: (id: string, title: string) => void;
  setModel: (id: string, model: DeepSeekModelId) => void;

  // Artifact actions (conversationId scopes artifacts to prevent cross-talk)
  openArtifact: (conversationId: string, artifact: Artifact) => void;
  updateArtifact: (conversationId: string, artifact: Artifact) => void;
  closeArtifact: () => void;

  // Data isolation — reset local state (e.g., on logout)
  clearData: () => void;
}

export const useChatStore = create<ChatState>()((set, get) => ({
  conversations: [],
  activeId: null,
  hydrated: true, // No persist middleware — always hydrated
  isLoading: false,
  artifacts: {},
  artifactOpen: false,
  messageSources: {},

  getActive: () => {
    const { conversations, activeId } = get();
    return conversations.find((c) => c.id === activeId) ?? null;
  },

  // ---- 服务端数据动作 ----

  loadConversations: async () => {
    // Guard: don't call API without auth token (prevents 401 loops)
    if (typeof window !== "undefined" && !localStorage.getItem("chatbot.access_token")) {
      set({ conversations: [], activeId: null, isLoading: false });
      return;
    }

    set({ isLoading: true });
    try {
      const data = await api.listConversations();
      const conversations: Conversation[] = data
        .map((c) => ({
          id: c.id,
          title: c.title,
          model: c.model as DeepSeekModelId,
          messages: [],
          createdAt: new Date(c.created_at).getTime(),
          updatedAt: new Date(c.updated_at).getTime(),
        }))
        .sort(
          (a, b) =>
            b.updatedAt - a.updatedAt || b.createdAt - a.createdAt,
        );

      set((s) => {
        // Keep existing messages in memory when possible
        const merged = conversations.map((c) => {
          const existing = s.conversations.find((ec) => ec.id === c.id);
          if (existing?.messages.length) {
            return { ...c, messages: existing.messages };
          }
          return c;
        });

        let activeId = s.activeId;
        if (merged.length === 0) {
          activeId = null;
        } else if (!activeId || !merged.some((c) => c.id === activeId)) {
          activeId = merged[0].id;
        }

        return {
          conversations: merged,
          activeId,
          hydrated: true,
          isLoading: false,
        };
      });
    } catch {
      // A failed API request must never create a local-only conversation.
      set({ hydrated: true, isLoading: false });
    }
  },

  createConversation: async () => {
    const model = get().getActive()?.model;
    try {
      const data = await api.createConversation({
        model: model || DEFAULT_MODEL,
      });
      const conv: Conversation = {
        id: data.id,
        title: data.title,
        model: data.model as DeepSeekModelId,
        messages: [],
        createdAt: new Date(data.created_at).getTime(),
        updatedAt: new Date(data.updated_at).getTime(),
      };
      set((s) => ({
        conversations: [conv, ...s.conversations.filter((c) => c.id !== conv.id)],
        activeId: conv.id,
        artifactOpen: false,
      }));
      return conv;
    } catch {
      return null;
    }
  },

  selectConversation: (id) =>
    set((s) => {
      const conversation = s.conversations.find((c) => c.id === id);
      if (!conversation) return s;
      const artifact = s.artifacts[id]
        ?? latestArtifactFromMessages(conversation.messages);
      return {
        activeId: id,
        artifacts: artifact
          ? { ...s.artifacts, [id]: artifact }
          : s.artifacts,
        artifactOpen: Boolean(artifact),
      };
    }),

  deleteConversation: async (id) => {
    try {
      await api.deleteConversation(id);
    } catch {
      // Proceed with local removal even if API fails
    }
    set((s) => {
      const next = s.conversations.filter((c) => c.id !== id);
      // Clean up artifact for deleted conversation
      const { [id]: _, ...remaining } = s.artifacts;
      if (next.length === 0) {
        return {
          conversations: [],
          activeId: null,
          artifacts: {},
          artifactOpen: false,
        };
      }
      return {
        conversations: next,
        activeId: s.activeId === id ? next[0].id : s.activeId,
        artifacts: remaining,
      };
    });
  },

  loadMessages: async (id: string) => {
    try {
      const data = await api.getConversation(id);
      const messages: ChatUIMessage[] = (data.messages || []).map((m) => ({
        id: m.id,
        role: m.role as ChatUIMessage["role"],
        parts: (m.parts || []).map((p) => {
          if (p.type === "text") {
            return { type: "text", text: p.text || "" };
          }
          if (p.type === "reasoning") {
            return { type: "reasoning", text: p.text || "", state: "complete" as const };
          }
          if (p.type === "sources") {
            const output = p.tool_output as { results?: Source[] } | null;
            return { type: "sources", sources: output?.results || [] };
          }
          // Tool parts
          return {
            type: p.type,
            toolCallId: p.tool_call_id || "",
            state: (p.tool_state as ChatUIMessage["parts"][number] extends { state: infer S } ? S : never) || "output-available",
            input: p.tool_input,
            output: p.tool_output,
            errorText: p.tool_error || undefined,
          };
        }),
        createdAt: new Date(m.created_at),
      }));

      // 新数据直接从最终回答的 sources part 恢复；工具输出分支兼容旧会话格式。
      const sourcesMap: Record<string, Source[]> = {};
      (data.messages || []).forEach((m) => {
        const msgId = messages.find((msg) => msg.id === m.id)?.id;
        if (!msgId) return;
        (m.parts || []).forEach((p) => {
          if (
            (p.type === "sources" ||
              p.type === "tool-deep_search" ||
              p.type === "tool-web_search") &&
            p.tool_output
          ) {
            const output = p.tool_output as { results?: Source[] };
            if (output.results?.length) {
              sourcesMap[msgId] = output.results;
            }
          }
        });
      });

      // 只有本地缓存为空才用 DB 覆盖：生成过程中内存比尚未落库的 assistant 消息新。
      const latestArtifact = latestArtifactFromMessages(messages);
      set((s) => {
        const canHydrateArtifact = latestArtifact && !s.streamingIds.has(id);
        return {
          conversations: s.conversations.map((c) =>
            c.id === id
              ? (c.messages.length === 0 ? { ...c, messages } : c)
              : c,
          ),
          artifacts: canHydrateArtifact
            ? { ...s.artifacts, [id]: latestArtifact }
            : s.artifacts,
          artifactOpen: canHydrateArtifact && s.activeId === id
            ? true
            : s.artifactOpen,
          messageSources: { ...s.messageSources, ...sourcesMap },
        };
      });
      return messages;
    } catch {
      return get().conversations.find((c) => c.id === id)?.messages || [];
    }
  },

  // ---- ChatView 合帧后的本地同步 ----

  setMessages: (id, messages) =>
    set((s) => {
      const index = s.conversations.findIndex((conversation) => conversation.id === id);
      if (index < 0 || s.conversations[index].messages === messages) return s;

      const current = s.conversations[index];
      const conversations = [...s.conversations];
      conversations[index] = {
        ...current,
        messages,
        updatedAt:
          messages.length === current.messages.length
            ? current.updatedAt
            : Date.now(),
      };
      return { conversations };
    }),

  setTitle: (id, title) => {
    // Also update on server
    api.updateConversation(id, { title }).catch(() => {});
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title, updatedAt: Date.now() } : c,
      ),
    }));
  },

  setModel: (id, model) => {
    // Also update on server
    api.updateConversation(id, { model }).catch(() => {});
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, model } : c,
      ),
    }));
  },

  // ---- Artifact 动作（全部带 conversationId 作用域） ----

  openArtifact: (conversationId, artifact) =>
    set((s) => ({
      artifacts: { ...s.artifacts, [conversationId]: artifact },
      artifactOpen: true,
    })),
  updateArtifact: (conversationId, artifact) =>
    set((s) => {
      const current = s.artifacts[conversationId];
      // 同一工具调用持续覆盖；新一轮 streaming Artifact 可以替换旧的已完成工件。
      if (!current) return { artifacts: { ...s.artifacts, [conversationId]: artifact } };
      if (current.id === artifact.id) return { artifacts: { ...s.artifacts, [conversationId]: artifact } };
      if (!current.streaming && artifact.streaming) return { artifacts: { ...s.artifacts, [conversationId]: artifact } };
      // 当前仍在流式生成时，迟到的其他旧工具事件不能把它覆盖。
      return s;
    }),
  closeArtifact: () => set({ artifactOpen: false }),

  setMessageSources: (messageId, sources) =>
    set((s) => ({
      messageSources: { ...s.messageSources, [messageId]: sources },
    })),

  clearData: () =>
    set({ conversations: [], activeId: null, artifacts: {}, artifactOpen: false, messageSources: {} }),

  // ---- 跨组件生命周期的流状态 ----

  streamingIds: new Set<string>(),

  markStreaming: (id) =>
    set((s) => {
      const next = new Set(s.streamingIds);
      next.add(id);
      return { streamingIds: next };
    }),

  markStreamDone: (id) =>
    set((s) => {
      const next = new Set(s.streamingIds);
      next.delete(id);
      return {
        streamingIds: next,
        _streamAborts: { ...s._streamAborts, [id]: null },
      };
    }),

  isStreaming: (id) => get().streamingIds.has(id),

  // 每个对话只有一个订阅者控制器；新控制器注册时会取消旧订阅，防止双重消费。
  _streamAborts: {} as Record<string, AbortController | null>,
  abortStream: (id) => {
    const ctrl = get()._streamAborts[id];
    if (ctrl) {
      ctrl.abort();
    }
    // Always clean up streaming state to prevent UI lock from leaked IDs
    set((s) => {
      const next = new Set(s.streamingIds);
      next.delete(id);
      return {
        streamingIds: next,
        _streamAborts: { ...s._streamAborts, [id]: null },
      };
    });
  },
  setStreamAbort: (id, ctrl) => {
    const previous = get()._streamAborts[id];
    if (previous && previous !== ctrl) previous.abort();
    set((s) => ({
      _streamAborts: { ...s._streamAborts, [id]: ctrl },
    }));
  },
}));
