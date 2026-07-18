"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DeepSeekModelId } from "@/lib/models";
import type {
  Activity,
  ArtifactKind,
  ChatUIMessage,
  ContextStrategy,
  MessagePart,
  TextPart,
  ReasoningPart,
  ToolPartBase,
  SSEActivity,
  SSEContextStatus,
  SSEServerMessage,
  SearchMode,
} from "@/lib/types";
import { extractArtifactFields } from "@/lib/partial-json";
import {
  limitArtifactContent,
  MAX_ARTIFACT_TOOL_INPUT_CHARS,
} from "@/lib/artifact-security";
import { useChatStore } from "@/lib/store";
import { fetchWithAuth } from "@/lib/auth";
import {
  FrameDeltaBuffer,
  SSEFrameParser,
  StallWatchdog,
  createPaintScheduler,
  dispatchSSEFrames,
  type PaintScheduler,
  type ParsedSSEFrame,
  type SSECursor,
} from "@/lib/sse-stream";
import {
  createSSESessionStore,
  type SSEDraftRecord,
  type SSESessionRecord,
  type SSESessionStore,
} from "@/lib/sse-session";

/**
 * 聊天流的浏览器端编排层，按职责可分为四段：
 *
 * 1. 连接：POST 建流，断流后携带 Last-Event-ID 指数退避重连；
 * 2. 协议：TextDecoder + SSEFrameParser 抵抗 UTF-8/TCP 任意拆包；
 * 3. 渲染：所有 delta 先写 ref 双缓冲，再按 requestAnimationFrame 提交 React；
 * 4. 恢复：localStorage 保存 streamId、游标和草稿，刷新后继续同一次后端任务。
 *
 * 高频可变值放 ref，只有用户可见的帧快照进入 state。这是避免逐 token 重渲染的
 * 核心边界，也符合 React 中“瞬时值不应驱动组件树”的原则。
 */

export type ChatStatus = "ready" | "submitted" | "streaming" | "error";

export interface UseChatStreamOptions {
  conversationId: string;
  initialMessages: ChatUIMessage[];
}

export interface UseChatStreamReturn {
  messages: ChatUIMessage[];
  activities: Activity[];
  sendMessage: (
    message: { text: string },
    opts?: { body?: { model?: DeepSeekModelId; searchMode?: SearchMode } },
  ) => void;
  stop: () => void;
  status: ChatStatus;
  error: Error | null;
}

const MAX_RECONNECT_ATTEMPTS = 4;
const RECONNECT_BASE_DELAY_MS = 250;
// 后端每 15 秒发送 keepalive；连续 3 个周期无任何字节视为半开连接并强制续传。
const STALL_TIMEOUT_MS = 45_000;
// localStorage 是同步 I/O，草稿必须节流，否则会阻塞每个绘制帧。
const DRAFT_PERSIST_INTERVAL_MS = 300;
// On refresh-resume, wait this long for DB history before restoring the
// draft onto an empty base (degraded mode).
const RESUME_DB_WAIT_MS = 5_000;

// 单例 Store 的键包含 conversationId；不同对话的断点和草稿不会串线。
const sessionStore = createSSESessionStore();

function generateId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useChatStream({
  conversationId,
  initialMessages,
}: UseChatStreamOptions): UseChatStreamReturn {
  // --- State ---
  const [messages, setMessages] = useState<ChatUIMessage[]>(initialMessages);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [error, setError] = useState<Error | null>(null);

  // AbortController for cancelling in-flight requests
  const abortRef = useRef<AbortController | null>(null);
  const activeStreamIdRef = useRef<string | null>(null);

  // 流式工作区全部放 ref：网络 delta 到达不会直接触发 React 重渲染。
  const streamingMessageRef = useRef<ChatUIMessage | null>(null);
  const currentTextPartRef = useRef<TextPart | null>(null);
  const currentReasoningPartRef = useRef<ReasoningPart | null>(null);
  const textDeltaBufferRef = useRef<FrameDeltaBuffer | null>(null);
  const reasoningDeltaBufferRef = useRef<FrameDeltaBuffer | null>(null);
  const toolCallsRef = useRef<Map<string, ToolPartBase>>(new Map());
  const toolDeltaBuffersRef = useRef<Map<string, FrameDeltaBuffer>>(new Map());
  const paintSchedulerRef = useRef<PaintScheduler | null>(null);

  // 刷新续传状态：事件游标在整个 Hook 生命周期内单调前进。
  const sessionStoreRef = useRef<SSESessionStore>(sessionStore);
  const sessionActiveRef = useRef(false);
  const eventCursorRef = useRef<SSECursor>({ lastEventId: "" });
  const draftPersistTimerRef = useRef<number | null>(null);
  const resumeAttemptedRef = useRef(false);
  const [dbWaitExpired, setDbWaitExpired] = useState(false);

  // Latest messages ref for sendMessage
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  function cancelScheduledRender() {
    paintSchedulerRef.current?.cancel();
  }

  // 延迟创建调度器，未开始流式生成时不注册无意义的 rAF/timer。
  function getPaintScheduler(): PaintScheduler {
    if (!paintSchedulerRef.current) {
      paintSchedulerRef.current = createPaintScheduler(() => {
        commitStreamingMessage();
      });
    }
    return paintSchedulerRef.current;
  }

  // --- Incremental persistence (增量存储) ---

  function clearStreamSession() {
    sessionActiveRef.current = false;
    if (draftPersistTimerRef.current !== null) {
      window.clearTimeout(draftPersistTimerRef.current);
      draftPersistTimerRef.current = null;
    }
    sessionStoreRef.current.clear(conversationId);
  }

  function persistDraftNow() {
    if (!sessionActiveRef.current) return;
    const msg = streamingMessageRef.current;
    if (!msg || msg.parts.length === 0) return;
    sessionStoreRef.current.saveDraft(conversationId, {
      messageId: msg.id,
      parts: msg.parts,
      updatedAt: Date.now(),
    });
  }

  function scheduleDraftPersist() {
    if (draftPersistTimerRef.current !== null) return;
    draftPersistTimerRef.current = window.setTimeout(() => {
      draftPersistTimerRef.current = null;
      persistDraftNow();
    }, DRAFT_PERSIST_INTERVAL_MS);
  }

  function resetStreamingState() {
    cancelScheduledRender();
    streamingMessageRef.current = null;
    currentTextPartRef.current = null;
    currentReasoningPartRef.current = null;
    textDeltaBufferRef.current = null;
    reasoningDeltaBufferRef.current = null;
    toolCallsRef.current = new Map();
    toolDeltaBuffersRef.current = new Map();
    setActivities([]);
  }

  // --- Reset when conversation changes ---
  useEffect(() => {
    setMessages(initialMessages);
    setError(null);
    setStatus("ready");
    resumeAttemptedRef.current = false;
    resetStreamingState();

    return () => {
      // 后端 Graph 生产者与 HTTP 订阅解耦；这里 abort 只释放当前 reader。
      // 旧组件若继续消费，HMR/重挂载时会出现双订阅，因此卸载必须立即断开它。
      if (draftPersistTimerRef.current !== null) {
        window.clearTimeout(draftPersistTimerRef.current);
        draftPersistTimerRef.current = null;
      }
      if (sessionActiveRef.current && streamingMessageRef.current) {
        commitStreamingMessage();
        persistDraftNow();
      }
      const controller = abortRef.current;
      controller?.abort();
      if (abortRef.current === controller) abortRef.current = null;
      activeStreamIdRef.current = null;
      resetStreamingState();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // --- Sync DB-loaded messages into hook state ---
  // loadMessages is async: initialMessages may be empty on first render
  // and populated later via the store without a conversationId change.
  // This effect bridges that gap without overwriting streaming data.
  const storeLoadAppliedRef = useRef(false);
  useEffect(() => {
    // Reset flag when conversation changes
    storeLoadAppliedRef.current = false;
  }, [conversationId]);

  useEffect(() => {
    // Only apply if DB messages arrived AND local state is empty
    if (storeLoadAppliedRef.current) return;
    if (initialMessages.length === 0) return;
    // Guard: don't overwrite if we already have streaming/local messages
    if (messagesRef.current.length > 0) return;

    storeLoadAppliedRef.current = true;
    setMessages(initialMessages);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMessages]);

  // --- Flush the latest draft before the page goes away (refresh/close) ---
  useEffect(() => {
    const flush = () => {
      if (draftPersistTimerRef.current !== null) {
        window.clearTimeout(draftPersistTimerRef.current);
        draftPersistTimerRef.current = null;
      }
      if (!sessionActiveRef.current || !streamingMessageRef.current) return;
      // Synchronously publish pending deltas so the persisted draft is current.
      commitStreamingMessage();
      persistDraftNow();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (draftPersistTimerRef.current !== null) {
        window.clearTimeout(draftPersistTimerRef.current);
        draftPersistTimerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // --- Arm the DB-wait timer only when a resumable session exists ---
  useEffect(() => {
    setDbWaitExpired(false);
    if (!sessionStoreRef.current.loadSession(conversationId)) return;
    const timer = window.setTimeout(
      () => setDbWaitExpired(true),
      RESUME_DB_WAIT_MS,
    );
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // --- Resume an in-flight stream after a page refresh (断点续传) ---
  useEffect(() => {
    if (resumeAttemptedRef.current) return;
    const session = sessionStoreRef.current.loadSession(conversationId);
    if (!session) {
      resumeAttemptedRef.current = true;
      return;
    }
    // Wait for DB history so the user turn precedes the restored draft.
    if (initialMessages.length === 0 && !dbWaitExpired) return;
    resumeAttemptedRef.current = true;
    void resumeStreamSession(session);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, initialMessages, dbWaitExpired]);

  // --- ref 双缓冲 → React 消息快照 ---
  // Zustand 同步留给 ChatView effect；不能在 React setState updater 内再 set Store，
  // 否则会在渲染阶段同步通知另一棵订阅树。
  function flushToolDeltaBuffer(toolCallId: string, final = false) {
    const buffer = toolDeltaBuffersRef.current.get(toolCallId);
    const toolCall = toolCallsRef.current.get(toolCallId);
    if (!buffer?.hasPending || !toolCall) return;

    const rawInput = buffer.flush(final);
    const updated: ToolPartBase = { ...toolCall, input: rawInput };
    toolCallsRef.current.set(toolCallId, updated);

    // Artifact 参数也按绘制帧解析/写 Store，大文档不会按网络 delta 刷新整个侧栏。
    if (updated.type === "tool-create_artifact") {
      const partial = extractArtifactFields(rawInput);
      const store = useChatStore.getState();
      if (partial.title || partial.content) {
        store.updateArtifact(conversationId, {
          id: toolCallId,
          title: partial.title || "未命名工件",
          kind: (partial.kind as ArtifactKind) || "code",
          language: partial.language,
          content: limitArtifactContent(partial.content),
          streaming: true,
        });
      }
      if (!store.artifactOpen && (partial.title || partial.content)) {
        store.openArtifact(conversationId, {
          id: toolCallId,
          title: partial.title || "未命名工件",
          kind: (partial.kind as ArtifactKind) || "code",
          language: partial.language,
          content: limitArtifactContent(partial.content),
          streaming: true,
        });
      }
    }
  }

  function flushPendingDeltas(final = false) {
    if (currentTextPartRef.current && textDeltaBufferRef.current?.hasPending) {
      currentTextPartRef.current = {
        ...currentTextPartRef.current,
        text: textDeltaBufferRef.current.flush(final),
      };
    }
    if (
      currentReasoningPartRef.current &&
      reasoningDeltaBufferRef.current?.hasPending
    ) {
      currentReasoningPartRef.current = {
        ...currentReasoningPartRef.current,
        text: reasoningDeltaBufferRef.current.flush(final),
      };
    }
    for (const toolCallId of toolDeltaBuffersRef.current.keys()) {
      flushToolDeltaBuffer(toolCallId, final);
    }
  }

  function commitStreamingMessage() {
    if (!streamingMessageRef.current) return;
    flushPendingDeltas();

    const parts: MessagePart[] = [];
    if (currentReasoningPartRef.current?.text) {
      parts.push({ ...currentReasoningPartRef.current });
    }
    if (currentTextPartRef.current?.text) {
      parts.push({ ...currentTextPartRef.current });
    }
    toolCallsRef.current.forEach((tc) => parts.push({ ...tc }));

    const updated: ChatUIMessage = {
      ...streamingMessageRef.current,
      parts,
    };
    streamingMessageRef.current = updated;

    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === updated.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = updated;
        return next;
      }
      return [...prev, updated];
    });
    // 可见帧提交后节流保存草稿，刷新时最多损失一个很短的未落盘窗口。
    scheduleDraftPersist();
  }

  // 一个浏览器帧可能收到大量小 delta：ref 保存无损全量，React 每帧最多发布一次。
  function scheduleStreamingMessage() {
    getPaintScheduler().schedule();
  }

  function finalizeStreamingMessage() {
    cancelScheduledRender();
    const msg = streamingMessageRef.current;
    if (!msg) return;
    flushPendingDeltas(true);

    const parts: MessagePart[] = [];
    if (currentReasoningPartRef.current?.text) {
      parts.push({ ...currentReasoningPartRef.current, state: "complete" as const });
    }
    if (currentTextPartRef.current?.text) {
      parts.push({ ...currentTextPartRef.current });
    }
    toolCallsRef.current.forEach((tc) => parts.push({ ...tc }));

    const finalized: ChatUIMessage = {
      ...msg,
      parts: parts.length > 0 ? parts : msg.parts,
    };

    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === finalized.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = finalized;
        return next;
      }
      return [...prev, finalized];
    });
    resetStreamingState();
  }

  // --- Process SSE event ---
  function handleServerEvent(data: SSEServerMessage, signal: AbortSignal) {
    // 每条流检查自己的 AbortSignal。切换对话后即使旧 read() 恰好返回，也不能再推进
    // 共享游标或写入新对话；独立 signal 比共享 boolean 更能避免竞态。
    if (signal.aborted) return;

    switch (data.type) {
      case "text_start": {
        // A tool call and the final answer are separate main-agent messages.
        if (streamingMessageRef.current && streamingMessageRef.current.id !== data.messageId) {
          finalizeStreamingMessage();
        }
        currentTextPartRef.current = { type: "text", text: "" };
        textDeltaBufferRef.current = new FrameDeltaBuffer();
        if (!streamingMessageRef.current) {
          streamingMessageRef.current = {
            id: data.messageId,
            role: "assistant",
            parts: [],
          };
        }
        break;
      }
      case "text_delta": {
        if (currentTextPartRef.current) {
          textDeltaBufferRef.current?.append(data.delta);
          scheduleStreamingMessage();
        }
        break;
      }
      case "text_end":
        break;

      case "reasoning_start": {
        // Finalize the preceding main-agent tool-call message when needed.
        if (streamingMessageRef.current && streamingMessageRef.current.id !== data.messageId) {
          finalizeStreamingMessage();
        }
        currentReasoningPartRef.current = {
          type: "reasoning",
          text: "",
          state: "streaming",
        };
        reasoningDeltaBufferRef.current = new FrameDeltaBuffer();
        if (!streamingMessageRef.current) {
          streamingMessageRef.current = {
            id: data.messageId,
            role: "assistant",
            parts: [],
          };
        }
        break;
      }
      case "reasoning_delta": {
        if (currentReasoningPartRef.current) {
          reasoningDeltaBufferRef.current?.append(data.delta);
          scheduleStreamingMessage();
        }
        break;
      }
      case "reasoning_end":
        break;

      case "tool_call_start": {
        // Same main-agent message boundary check as text_start.
        if (streamingMessageRef.current && streamingMessageRef.current.id !== data.messageId) {
          finalizeStreamingMessage();
        }
        const tc: ToolPartBase = {
          type: `tool-${data.toolName}`,
          toolCallId: data.toolCallId,
          state: "input-streaming",
          input: "",
        };
        toolCallsRef.current.set(data.toolCallId, tc);
        toolDeltaBuffersRef.current.set(
          data.toolCallId,
          new FrameDeltaBuffer(
            tc.type === "tool-create_artifact"
              ? MAX_ARTIFACT_TOOL_INPUT_CHARS
              : Number.POSITIVE_INFINITY,
          ),
        );
        if (!streamingMessageRef.current) {
          streamingMessageRef.current = {
            id: data.messageId,
            role: "assistant",
            parts: [],
          };
        }
        scheduleStreamingMessage();
        break;
      }
      case "tool_call_delta": {
        const buffer = toolDeltaBuffersRef.current.get(data.toolCallId);
        if (buffer) {
          buffer.append(data.delta);
          scheduleStreamingMessage();
        }
        break;
      }
      case "tool_call_end": {
        flushToolDeltaBuffer(data.toolCallId, true);
        const tc = toolCallsRef.current.get(data.toolCallId);
        if (tc) {
          let parsedInput: unknown = tc.input;
          if (typeof tc.input === "string" && tc.input) {
            try { parsedInput = JSON.parse(tc.input); } catch { /* keep raw */ }
          }
          toolCallsRef.current.set(data.toolCallId, {
            ...tc,
            input: parsedInput,
            state: "input-available",
          });

          // 完整 JSON 到齐后把 Artifact 标为完成，侧栏此时才允许挂载 iframe。
          if (
            tc.type === "tool-create_artifact" &&
            typeof parsedInput === "object" &&
            parsedInput
          ) {
            const pi = parsedInput as Record<string, unknown>;
            useChatStore.getState().updateArtifact(conversationId, {
              id: data.toolCallId,
              title: String(pi.title || "未命名工件"),
              kind: (pi.kind as ArtifactKind) || "code",
              language:
                typeof pi.language === "string" ? pi.language : undefined,
              content: limitArtifactContent(
                typeof pi.content === "string" ? pi.content : "",
              ),
              streaming: false,
            });
          }

          scheduleStreamingMessage();
        }
        break;
      }
      case "tool_result": {
        const tc = toolCallsRef.current.get(data.toolCallId);
        if (tc) {
          toolCallsRef.current.set(data.toolCallId, {
            ...tc,
            output: data.result,
            errorText: data.error || undefined,
            state: data.error ? "output-error" : "output-available",
          });

          scheduleStreamingMessage();
        }
        break;
      }

      case "sources": {
        // The backend emits this only for the final answer message.
        useChatStore.getState().setMessageSources(data.messageId, data.sources);
        break;
      }

      case "activity": {
        const activityData = data as SSEActivity;
        const newActivity: Activity = {
          kind: activityData.kind,
          message: activityData.message,
          timestamp: Date.now(),
        };
        setActivities((prev) => [...prev, newActivity]);
        break;
      }

      case "context_status": {
        const contextData = data as SSEContextStatus;
        if (contextData.strategies.length > 0 || contextData.overflowed) {
          const strategyLabels: Record<ContextStrategy, string> = {
            microcompact: "工具结果瘦身",
            context_collapse: "分段摘要",
            session_memory: "会话记忆",
            full_compact: "全量压缩",
            ptl_truncation: "最早轮次截断",
          };
          const labels = contextData.strategies
            .map((strategy) => strategyLabels[strategy] ?? strategy)
            .join(" → ");
          setActivities((prev) => [...prev, {
            kind: "compacting",
            message: `${labels || "上下文仍超限"}：约 ${contextData.estimatedTokensBefore} → ${contextData.estimatedTokensAfter} tokens`,
            timestamp: Date.now(),
          }]);
        }
        break;
      }

      case "trace_summary":
        // Persisted by the backend and consumed by the observability page.
        // The chat surface intentionally stays focused on the answer.
        break;

      case "done": {
        // Finalize whatever message is currently streaming.
        // The done event's messageId is from the graph runner, not the
        // main-agent node, so it won't match. The current streaming
        // message was already synced during streaming
        // via scheduled frame commits — we just need to reset the streaming
        // state so the hook is ready for the next message.
        finalizeStreamingMessage();
        resetStreamingState();
        clearStreamSession();
        setStatus("ready");
        useChatStore.getState().markStreamDone(conversationId);
        break;
      }

      case "error": {
        finalizeStreamingMessage();
        resetStreamingState();
        clearStreamSession();
        setError(new Error(data.message));
        setStatus("error");
        useChatStore.getState().markStreamDone(conversationId);
        break;
      }

      case "pong":
        break;
    }
  }

  // --- 字节流 → UTF-8 文本 → SSE 帧 → 业务事件 ---
  async function readSSEStream(
    response: Response,
    signal: AbortSignal,
    cursor: SSECursor,
  ): Promise<boolean> {
    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("无法读取响应流");
    }

    const decoder = new TextDecoder();
    const parser = new SSEFrameParser();
    let terminal = false;
    let droppedFrames = 0;
    let stalled = false;
    let persistedEventId = cursor.lastEventId;

    // 半开连接会让 reader.read() 永久 pending；看门狗取消 reader 后统一走普通重连路径。
    const watchdog = new StallWatchdog(STALL_TIMEOUT_MS, () => {
      stalled = true;
      reader.cancel().catch(() => undefined);
    });
    watchdog.ping(); // arm from connect; the first byte may itself be slow

    function processFrames(frames: ParsedSSEFrame[]) {
      // abort 与已完成的 read() 可能竞态：旧订阅者不处理帧，也不能推进新订阅者的游标。
      if (signal.aborted) return;
      // 游标、坏帧和尾帧策略集中在纯函数中，让生产 Hook 与 eval 使用完全同一实现。
      const outcome = dispatchSSEFrames<SSEServerMessage>(
        frames,
        cursor,
        (event) => handleServerEvent(event, signal),
        (event) => event.type === "done" || event.type === "error",
        (frame, cause) => {
          console.warn("[SSE] 丢弃损坏的帧", frame.id ?? "?", cause);
        },
      );
      terminal = terminal || outcome.terminal;
      droppedFrames += outcome.dropped;
      // 游标一推进就持久化；此时刷新最多重放当前小批次，重复帧仍会被游标去重。
      if (sessionActiveRef.current && cursor.lastEventId !== persistedEventId) {
        persistedEventId = cursor.lastEventId;
        sessionStoreRef.current.updateCursor(
          conversationId,
          cursor.lastEventId,
          cursor.retryMs,
        );
      }
    }

    try {
      while (true) {
        if (signal.aborted) break;

        const { done, value } = await reader.read();
        if (done) break;
        watchdog.ping(); // any byte (data or keepalive) proves liveness
        processFrames(parser.push(decoder.decode(value, { stream: true })));
        if (terminal) {
          await reader.cancel().catch(() => undefined);
          break;
        }
      }
      const trailingText = decoder.decode();
      if (trailingText) processFrames(parser.push(trailingText));
      processFrames(parser.finish());

      if (signal.aborted) return terminal;
      if (stalled) {
        setActivities((prev) => [...prev, {
          kind: "analyzing",
          message: "连接停滞超时，正在从断点续传…",
          timestamp: Date.now(),
        }]);
      }
      if (droppedFrames > 0) {
        setActivities((prev) => [...prev, {
          kind: "analyzing",
          message: `已跳过 ${droppedFrames} 个损坏的数据帧（连接不稳定）`,
          timestamp: Date.now(),
        }]);
      }
      return terminal;
    } finally {
      watchdog.disarm();
      reader.releaseLock();
    }
  }

  function waitForReconnect(delayMs: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const handleAbort = () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      const timer = window.setTimeout(() => {
        signal.removeEventListener("abort", handleAbort);
        resolve();
      }, delayMs);
      signal.addEventListener("abort", handleAbort, { once: true });
    });
  }

  async function connectStream(
    requestBody: string,
    signal: AbortSignal,
    cursor: SSECursor,
  ): Promise<void> {
    let lastFailure: unknown;

    for (let attempt = 0; attempt <= MAX_RECONNECT_ATTEMPTS; attempt += 1) {
      try {
        const response = await fetchWithAuth("/api/chat", {
          method: "POST",
          headers: cursor.lastEventId
            ? { "Last-Event-ID": cursor.lastEventId }
            : undefined,
          body: requestBody,
          signal,
        });
        if (!response.ok) {
          const detail = await response.text().catch(() => "");
          const failure = new Error(`请求失败 (${response.status}): ${detail}`);
          if (response.status < 500) throw Object.assign(failure, { retryable: false });
          throw failure;
        }

        const terminal = await readSSEStream(response, signal, cursor);
        if (terminal) return;
        lastFailure = new Error("服务器在完成事件前关闭了连接");
      } catch (failure) {
        if (signal.aborted) throw failure;
        if ((failure as { retryable?: boolean }).retryable === false) throw failure;
        lastFailure = failure;
      }

      if (attempt >= MAX_RECONNECT_ATTEMPTS) break;
      const delay = cursor.retryMs
        ?? Math.min(RECONNECT_BASE_DELAY_MS * 2 ** attempt, 4_000);
      setActivities((prev) => [...prev, {
        kind: "analyzing",
        message: `连接中断，正在从事件 ${cursor.lastEventId || "0"} 续传（${attempt + 1}/${MAX_RECONNECT_ATTEMPTS}）`,
        timestamp: Date.now(),
      }]);
      await waitForReconnect(delay, signal);
    }

    throw lastFailure instanceof Error
      ? lastFailure
      : new Error("流式连接重试失败");
  }

  // --- Refresh-resume helpers (断点续传) ---

  async function probeStream(streamId: string): Promise<boolean> {
    try {
      const res = await fetchWithAuth(
        `/api/chat/stream/${encodeURIComponent(streamId)}/status`,
        { method: "GET" },
      );
      return res.status === 204;
    } catch {
      return false;
    }
  }

  /** 从草稿重建 ref 工作区，后续 delta 会无缝追加在已显示内容之后。 */
  function restoreDraftRefs(draft: SSEDraftRecord) {
    streamingMessageRef.current = {
      id: draft.messageId,
      role: "assistant",
      parts: draft.parts,
    };
    const textPart = [...draft.parts].reverse().find(
      (p): p is TextPart => p.type === "text",
    );
    if (textPart) {
      currentTextPartRef.current = { type: "text", text: textPart.text };
      const buffer = new FrameDeltaBuffer();
      buffer.seed(textPart.text);
      textDeltaBufferRef.current = buffer;
    }
    const reasoningPart = [...draft.parts].reverse().find(
      (p): p is ReasoningPart => p.type === "reasoning",
    );
    if (reasoningPart) {
      currentReasoningPartRef.current = {
        type: "reasoning",
        text: reasoningPart.text,
        state: reasoningPart.state ?? "streaming",
      };
      const buffer = new FrameDeltaBuffer();
      buffer.seed(reasoningPart.text);
      reasoningDeltaBufferRef.current = buffer;
    }
    for (const part of draft.parts) {
      if (part.type === "text" || part.type === "reasoning" || part.type === "sources") {
        continue;
      }
      const toolPart = part as ToolPartBase;
      toolCallsRef.current.set(toolPart.toolCallId, { ...toolPart });
      if (toolPart.state === "input-streaming") {
        const buffer = new FrameDeltaBuffer(
          toolPart.type === "tool-create_artifact"
            ? MAX_ARTIFACT_TOOL_INPUT_CHARS
            : Number.POSITIVE_INFINITY,
        );
        buffer.seed(
          typeof toolPart.input === "string"
            ? toolPart.input
            : JSON.stringify(toolPart.input ?? ""),
        );
        toolDeltaBuffersRef.current.set(toolPart.toolCallId, buffer);
      }
    }
    setMessages((prev) =>
      prev.some((m) => m.id === draft.messageId)
        ? prev
        : [
            ...prev,
            {
              id: draft.messageId,
              role: "assistant" as const,
              parts: draft.parts,
            },
          ],
    );
  }

  /** 后端已丢失流时保留局部回答，但绝不重新发送原任务造成重复生成。 */
  async function abandonStreamSession(
    draft: SSEDraftRecord | null,
    note: string,
  ) {
    clearStreamSession();
    if (draft && draft.parts.length > 0) {
      restoreDraftRefs(draft);
      finalizeStreamingMessage();
      setActivities((prev) => [
        ...prev,
        { kind: "analyzing", message: note, timestamp: Date.now() },
      ]);
    } else {
      // No local partial: the run may have completed while the page was
      // closed — reconcile with the DB so a finished answer still appears.
      setActivities((prev) => [
        ...prev,
        { kind: "analyzing", message: note, timestamp: Date.now() },
      ]);
      const fresh = await useChatStore.getState().loadMessages(conversationId);
      if (fresh.length > messagesRef.current.length) {
        setMessages(fresh);
      }
    }
    setStatus("ready");
    useChatStore.getState().markStreamDone(conversationId);
  }

  async function resumeStreamSession(session: SSESessionRecord) {
    const draft = sessionStoreRef.current.loadDraft(conversationId);

    // 首事件前刷新没有游标，盲目 POST 可能在后端重启后创建重复任务，因此先探测。
    // 已有游标的 POST 是幂等续订：命中日志或返回 409，不会悄悄重跑。
    if (!session.lastEventId) {
      const alive = await probeStream(session.streamId);
      if (!alive) {
        await abandonStreamSession(
          draft,
          "上次生成已中断（服务已重启或会话过期），以上为中断前内容",
        );
        return;
      }
    }

    sessionActiveRef.current = true;
    if (draft) restoreDraftRefs(draft);
    setError(null);
    setStatus("streaming");
    useChatStore.getState().markStreaming(conversationId);

    const abortController = new AbortController();
    abortRef.current = abortController;
    useChatStore.getState().setStreamAbort(conversationId, abortController);
    activeStreamIdRef.current = session.streamId;
    // Keep one cursor object for the hook lifetime. Strict Mode/HMR can start
    // a replacement resume before the previous promise settles; both readers
    // then consult the same monotonically advancing cursor.
    const cursor = eventCursorRef.current;
    const currentId = Number(cursor.lastEventId || 0);
    const restoredId = Number(session.lastEventId || 0);
    if (!cursor.lastEventId || restoredId > currentId) {
      cursor.lastEventId = session.lastEventId;
    }
    if (cursor.retryMs === undefined) cursor.retryMs = session.retryMs;

    setActivities((prev) => [
      ...prev,
      {
        kind: "analyzing",
        message: `页面刷新，正在从断点续传（事件 ${session.lastEventId || "0"}）…`,
        timestamp: Date.now(),
      },
    ]);

    connectStream(session.requestBody, abortController.signal, cursor)
      .catch((err) => {
        if (err.name === "AbortError") return;
        // 409 / replay-expired land here: keep partial content visible.
        finalizeStreamingMessage();
        clearStreamSession();
        setError(new Error(`续传失败: ${err.message}`));
        setStatus("error");
        useChatStore.getState().markStreamDone(conversationId);
      })
      .finally(() => {
        if (abortRef.current === abortController) abortRef.current = null;
        if (activeStreamIdRef.current === session.streamId) {
          activeStreamIdRef.current = null;
        }
      });
  }

  // --- sendMessage ---
  const sendMessage = useCallback(
    (
      msg: { text: string },
      opts?: { body?: { model?: DeepSeekModelId; searchMode?: SearchMode } },
    ) => {
      const text = msg.text.trim();
      if (!text) return;

      // Cancel any in-flight request for this conversation
      // (uses store-tracked controller so it works across mount/unmount cycles)
      abortRef.current?.abort();
      useChatStore.getState().abortStream(conversationId);

      const userMsg: ChatUIMessage = {
        id: generateId(),
        role: "user",
        parts: [{ type: "text", text }],
        createdAt: new Date(),
      };
      const updatedMessages = [...messagesRef.current, userMsg];
      setMessages(updatedMessages);

      resetStreamingState();
      setError(null);
      setStatus("streaming");

      // Track that this conversation is streaming (survives unmount)
      useChatStore.getState().markStreaming(conversationId);

      const abortController = new AbortController();
      abortRef.current = abortController;
      // Register in store so re-entering this conversation can abort the old stream
      useChatStore.getState().setStreamAbort(conversationId, abortController);

      // Prefer conversation_id mode (backend loads history from DB)
      // Fall back to full messages for backward compat
      const streamMessages = updatedMessages.map((m) => ({
        role: m.role,
        content: "",
        parts: m.parts,
      }));
      const streamId = generateId();
      activeStreamIdRef.current = streamId;
      const requestBody = JSON.stringify({
        stream_id: streamId,
        conversation_id: conversationId,
        new_message: {
          role: "user",
          content: text,
          parts: [{ type: "text", text }],
        },
        messages: streamMessages, // fallback for backward compat
        model: opts?.body?.model,
        search_mode: opts?.body?.searchMode ?? "auto",
      });

      // 首字节到达前先保存会话，连接建立阶段刷新也能探测并安全续订/放弃。
      clearStreamSession();
      sessionStoreRef.current.saveSession(conversationId, {
        streamId,
        requestBody,
        lastEventId: "",
        startedAt: Date.now(),
      });
      sessionActiveRef.current = true;
      const cursor = eventCursorRef.current;
      cursor.lastEventId = "";
      cursor.retryMs = undefined;

      connectStream(requestBody, abortController.signal, cursor)
        .catch((err) => {
          if (err.name === "AbortError") return;
          finalizeStreamingMessage();
          clearStreamSession();
          setError(new Error(`流式连接失败: ${err.message}`));
          setStatus("error");
          useChatStore.getState().markStreamDone(conversationId);
        })
        .finally(() => {
          if (abortRef.current === abortController) abortRef.current = null;
          if (activeStreamIdRef.current === streamId) activeStreamIdRef.current = null;
        });
    },
    [conversationId],
  );

  // --- stop ---
  const stop = useCallback(() => {
    const streamId = activeStreamIdRef.current;
    activeStreamIdRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    if (streamId) {
      fetchWithAuth(`/api/chat/stream/${encodeURIComponent(streamId)}`, {
        method: "DELETE",
      }).catch(() => undefined);
    }
    if (streamingMessageRef.current) {
      finalizeStreamingMessage();
    }
    clearStreamSession();
    setStatus("ready");
    // Clear store streaming flag so the UI doesn't stay locked in "busy" state
    useChatStore.getState().markStreamDone(conversationId);
  }, [conversationId]);

  return {
    messages,
    activities,
    sendMessage,
    stop,
    status,
    error,
  };
}
