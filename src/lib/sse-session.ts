/**
 * 正在生成的 SSE 会话持久化（刷新续传）。每个对话保存两条最小记录：
 *
 * - Session：streamId、原始 POST body、Last-Event-ID。刷新后用它重订阅后端日志；
 * - Draft：当前 assistant 消息快照。页面先恢复已显示内容，再从游标继续追加。
 *
 * Session 只更新小游标，Draft 由 Hook 节流写入，避免同步 localStorage 写操作阻塞
 * 动画帧。Storage 可注入，使 eval 能用内存实现验证“保存→刷新→恢复”全流程。
 */

import type { MessagePart } from "./types";

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface SSESessionRecord {
  streamId: string;
  /** Original POST body — replayed verbatim when resuming. */
  requestBody: string;
  /** Last-Event-ID cursor; "" means no event was consumed yet. */
  lastEventId: string;
  retryMs?: number;
  startedAt: number;
}

export interface SSEDraftRecord {
  messageId: string;
  parts: MessagePart[];
  updatedAt: number;
}

export interface SSESessionStore {
  saveSession(conversationId: string, record: SSESessionRecord): void;
  /** Cheap hot path: advances only the cursor fields of an existing session. */
  updateCursor(conversationId: string, lastEventId: string, retryMs?: number): void;
  loadSession(conversationId: string): SSESessionRecord | null;
  saveDraft(conversationId: string, draft: SSEDraftRecord): void;
  loadDraft(conversationId: string): SSEDraftRecord | null;
  /** Drop both records (terminal event, user stop, expired stream). */
  clear(conversationId: string): void;
}

const SESSION_PREFIX = "chatbot.sse.session.";
const DRAFT_PREFIX = "chatbot.sse.draft.";

/** SSR、隐私模式或禁用 Web Storage 时退化到内存，主聊天功能仍可使用。 */
function createMemoryStorage(): StorageLike {
  const map = new Map<string, string>();
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
    removeItem: (key) => void map.delete(key),
  };
}

function defaultStorage(): StorageLike {
  if (typeof window !== "undefined") {
    try {
      return window.localStorage;
    } catch {
      // storage access can throw in private mode — fall through
    }
  }
  return createMemoryStorage();
}

export function createSSESessionStore(
  storage: StorageLike = defaultStorage(),
): SSESessionStore {
  const sessionKey = (conversationId: string) => SESSION_PREFIX + conversationId;
  const draftKey = (conversationId: string) => DRAFT_PREFIX + conversationId;

  function readJson<T>(key: string): T | null {
    let raw: string | null;
    try {
      raw = storage.getItem(key);
    } catch {
      return null;
    }
    if (!raw) return null;
    try {
      return JSON.parse(raw) as T;
    } catch {
      // 截断写入或手工修改后的记录不可信：删除它，不能从错误游标继续拼接内容。
      try {
        storage.removeItem(key);
      } catch {
        /* ignore */
      }
      return null;
    }
  }

  function writeJson(key: string, value: unknown): void {
    try {
      storage.setItem(key, JSON.stringify(value));
    } catch {
      // 配额不足只让刷新续传降级，当前内存中的流式生成不能因此失败。
    }
  }

  return {
    saveSession(conversationId, record) {
      writeJson(sessionKey(conversationId), record);
    },

    updateCursor(conversationId, lastEventId, retryMs) {
      const existing = readJson<SSESessionRecord>(sessionKey(conversationId));
      if (!existing) return;
      writeJson(sessionKey(conversationId), {
        ...existing,
        lastEventId,
        ...(retryMs !== undefined ? { retryMs } : {}),
      });
    },

    loadSession(conversationId) {
      const record = readJson<SSESessionRecord>(sessionKey(conversationId));
      if (!record || typeof record.streamId !== "string" || !record.requestBody) {
        return null;
      }
      return record;
    },

    saveDraft(conversationId, draft) {
      writeJson(draftKey(conversationId), draft);
    },

    loadDraft(conversationId) {
      const record = readJson<SSEDraftRecord>(draftKey(conversationId));
      if (!record || typeof record.messageId !== "string" || !Array.isArray(record.parts)) {
        return null;
      }
      return record;
    },

    clear(conversationId) {
      try {
        storage.removeItem(sessionKey(conversationId));
        storage.removeItem(draftKey(conversationId));
      } catch {
        /* ignore */
      }
    },
  };
}
