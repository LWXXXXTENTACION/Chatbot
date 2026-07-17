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

// ============================================================
// Hook: useChatStream
//
// Sends messages via HTTP POST, receives streaming response as
// SSE (Server-Sent Events) via the ReadableStream API.
// ============================================================

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

  // Accumulated streaming state (all via refs — no re-renders per delta)
  const streamingMessageRef = useRef<ChatUIMessage | null>(null);
  const currentTextPartRef = useRef<TextPart | null>(null);
  const currentReasoningPartRef = useRef<ReasoningPart | null>(null);
  const toolCallsRef = useRef<Map<string, ToolPartBase>>(new Map());

  // Latest messages ref for sendMessage
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  function resetStreamingState() {
    streamingMessageRef.current = null;
    currentTextPartRef.current = null;
    currentReasoningPartRef.current = null;
    toolCallsRef.current = new Map();
    setActivities([]);
  }

  // --- Reset when conversation changes ---
  useEffect(() => {
    setMessages(initialMessages);
    setError(null);
    setStatus("ready");
    resetStreamingState();

    return () => {
      // Do NOT abort the in-flight stream — it continues running in
      // the background and syncs to the Zustand store directly.
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

  // --- Sync refs → messages array for UI ---
  // Store sync happens in ChatView's useEffect so we don't call
  // Zustand set() inside React's setState updater (which React
  // invokes during the render phase — calling set() there triggers
  // a synchronous subscriber update, causing "Cannot update a
  // component while rendering a different component").
  function syncStreamingMessage() {
    if (!streamingMessageRef.current) return;

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
  }

  function finalizeStreamingMessage() {
    const msg = streamingMessageRef.current;
    if (!msg) return;

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
    // Drop events if this stream has been aborted (e.g. switched conversation).
    // Each stream checks its own AbortSignal, avoiding the race condition
    // that a shared boolean ref would have.
    if (signal.aborted) return;

    switch (data.type) {
      case "text_start": {
        // A tool call and the final answer are separate main-agent messages.
        if (streamingMessageRef.current && streamingMessageRef.current.id !== data.messageId) {
          finalizeStreamingMessage();
        }
        currentTextPartRef.current = { type: "text", text: "" };
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
          currentTextPartRef.current = {
            ...currentTextPartRef.current,
            text: currentTextPartRef.current.text + data.delta,
          };
          syncStreamingMessage();
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
          currentReasoningPartRef.current = {
            ...currentReasoningPartRef.current,
            text: currentReasoningPartRef.current.text + data.delta,
          };
          syncStreamingMessage();
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
        if (!streamingMessageRef.current) {
          streamingMessageRef.current = {
            id: data.messageId,
            role: "assistant",
            parts: [],
          };
        }
        syncStreamingMessage();
        break;
      }
      case "tool_call_delta": {
        const tc = toolCallsRef.current.get(data.toolCallId);
        if (tc) {
          const prevInput = (tc.input as string) || "";
          const rawInput = tc.type === "tool-create_artifact"
            ? (prevInput + data.delta).slice(0, MAX_ARTIFACT_TOOL_INPUT_CHARS)
            : prevInput + data.delta;
          const next: ToolPartBase = { ...tc, input: rawInput };

          // Incremental artifact parsing: extract fields from partial JSON
          // so ArtifactCard/ArtifactPanel show real data during streaming.
          if (tc.type === "tool-create_artifact") {
            const partial = extractArtifactFields(rawInput);
            // Push incremental content to the Zustand store so
            // ArtifactPanel receives real-time updates.
            const store = useChatStore.getState();
            if (partial.title || partial.content) {
              store.updateArtifact(conversationId, {
                id: data.toolCallId,
                title: partial.title || "未命名工件",
                kind: (partial.kind as ArtifactKind) || "code",
                language: partial.language,
                content: limitArtifactContent(partial.content),
                streaming: true,
              });
            }
            // Also open the panel on first content appearance
            if (!store.artifactOpen && (partial.title || partial.content)) {
              store.openArtifact(conversationId, {
                id: data.toolCallId,
                title: partial.title || "未命名工件",
                kind: (partial.kind as ArtifactKind) || "code",
                language: partial.language,
                content: limitArtifactContent(partial.content),
                streaming: true,
              });
            }
          }

          toolCallsRef.current.set(data.toolCallId, next);
          syncStreamingMessage();
        }
        break;
      }
      case "tool_call_end": {
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

          // Finalize artifact in store with streaming: false
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

          syncStreamingMessage();
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

          syncStreamingMessage();
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
        // via syncStreamingMessage — we just need to reset the streaming
        // state so the hook is ready for the next message.
        finalizeStreamingMessage();
        resetStreamingState();
        setStatus("ready");
        useChatStore.getState().markStreamDone(conversationId);
        break;
      }

      case "error": {
        finalizeStreamingMessage();
        resetStreamingState();
        setError(new Error(data.message));
        setStatus("error");
        useChatStore.getState().markStreamDone(conversationId);
        break;
      }

      case "pong":
        break;
    }
  }

  // --- Parse SSE stream from fetch response ---
  async function readSSEStream(response: Response, signal: AbortSignal) {
    const reader = response.body?.getReader();
    if (!reader) {
      setError(new Error("无法读取响应流"));
      setStatus("error");
      useChatStore.getState().markStreamDone(conversationId);
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let errorReceived = false;

    try {
      while (true) {
        if (signal.aborted) break;

        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse complete SSE lines
        const lines = buffer.split("\n");
        buffer = lines.pop() || ""; // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const jsonStr = line.slice(6);
            if (!jsonStr) continue;
            try {
              const event = JSON.parse(jsonStr) as SSEServerMessage;
              if (event.type === "error") errorReceived = true;
              handleServerEvent(event, signal);
            } catch {
              // Skip unparseable events
            }
          }
          // Lines starting with ":" are SSE comments (keepalive), ignored
        }
      } // end SSE read loop

      // Defensive: if the stream ended without a "done" SSE event
      // (server closed connection prematurely), clean up streaming
      // state so the conversation doesn't stay locked in "busy".
      if (!signal.aborted) {
        // finalizeStreamingMessage() is a no-op if already finalized
        finalizeStreamingMessage();
        // Don't override error status set by an "error" SSE event
        if (!errorReceived) {
          setStatus("ready");
        }
        // markStreamDone is idempotent (Set.delete on absent key)
        useChatStore.getState().markStreamDone(conversationId);
      }
    } catch (err) {
      if (!signal.aborted) {
        setError(new Error(`流式读取中断: ${err instanceof Error ? err.message : String(err)}`));
        setStatus("error");
        useChatStore.getState().markStreamDone(conversationId);
      }
    } finally {
      reader.releaseLock();
    }
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

      fetchWithAuth("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: conversationId,
          new_message: {
            role: "user",
            content: text,
            parts: [{ type: "text", text }],
          },
          messages: streamMessages, // fallback for backward compat
          model: opts?.body?.model,
          search_mode: opts?.body?.searchMode ?? "auto",
        }),
        // Pass abort signal so this request can be cancelled on conversation switch
        signal: abortController.signal,
      } as RequestInit)
        .then(async (response) => {
          if (!response.ok) {
            if (response.status === 401) {
              setError(new Error("认证失败，请重新登录"));
            } else {
              const err = await response.text().catch(() => "");
              setError(new Error(`请求失败 (${response.status}): ${err}`));
            }
            setStatus("error");
            useChatStore.getState().markStreamDone(conversationId);
            return;
          }
          await readSSEStream(response, abortController.signal);
        })
        .catch((err) => {
          if (err.name === "AbortError") return;
          setError(new Error(`网络请求失败: ${err.message}`));
          setStatus("error");
          useChatStore.getState().markStreamDone(conversationId);
        });
    },
    [conversationId],
  );

  // --- stop ---
  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (streamingMessageRef.current) {
      finalizeStreamingMessage();
    }
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
