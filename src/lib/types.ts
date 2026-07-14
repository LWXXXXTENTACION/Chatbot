import type { DeepSeekModelId } from "./models";

// ============================================================
// Message Part Types (compatible with AI SDK v6 part shapes)
// These are manual definitions that match what the rendering
// components expect, without depending on the "ai" package.
// ============================================================

export type ToolState =
  | "input-streaming"
  | "input-available"
  | "approval-requested"
  | "output-available"
  | "output-error";

export interface TextPart {
  type: "text";
  text: string;
}

export interface ReasoningPart {
  type: "reasoning";
  text: string;
  state?: "streaming" | "complete";
}

export interface ToolPartBase {
  type: string; // `tool-${name}`
  toolCallId: string;
  state: ToolState;
  input?: unknown;
  output?: unknown;
  errorText?: string;
}

export type MessagePart = TextPart | ReasoningPart | ToolPartBase | SourcesPart;

export interface ChatUIMessage {
  id: string;
  role: "user" | "assistant" | "system";
  parts: MessagePart[];
  createdAt?: Date;
}

// ============================================================
// Conversation & Artifact (unchanged)
// ============================================================

export interface Conversation {
  id: string;
  title: string;
  model: DeepSeekModelId;
  messages: ChatUIMessage[];
  createdAt: number;
  updatedAt: number;
}

export type ArtifactKind = "code" | "html" | "markdown" | "svg";

export interface Artifact {
  /** toolCallId of the createArtifact invocation that produced it. */
  id: string;
  title: string;
  kind: ArtifactKind;
  language?: string;
  content: string;
  /** true while the tool input is still streaming. */
  streaming?: boolean;
}

// ============================================================
// Citation / Source Types
// ============================================================

export interface Source {
  title: string;
  url: string;
  content: string;
  score?: number;
}

export interface SourcesPart {
  type: "sources";
  sources: Source[];
}

// ============================================================
// Activity Timeline Types
// ============================================================

export interface Activity {
  kind: "searching" | "retrieved" | "analyzing" | "answering" | "rewriting";
  message: string;
  timestamp: number;
}

// ============================================================
// SSE Protocol Types (Server-Sent Events over HTTP POST)
//
// The frontend sends a POST request with chat messages, the
// backend streams SSE events (text/event-stream) in response.
// These types describe the JSON payloads in each SSE event line.
// ============================================================

/** Client → Server: send a chat request (POST body). */
export interface ChatSendRequest {
  conversation_id: string | null;
  new_message: {
    role: string;
    content: string;
    parts: MessagePart[];
  };
  messages: ChatHistoryMessage[];   // legacy: full history fallback
  model?: DeepSeekModelId;
}

export interface ChatHistoryMessage {
  role: "user" | "assistant" | "system";
  content: string | MessagePart[];
  parts?: MessagePart[];
}

// ---- Server → Client (SSE event `data:` lines) ----

export interface SSETextStart {
  type: "text_start";
  messageId: string;
}

export interface SSETextDelta {
  type: "text_delta";
  messageId: string;
  delta: string;
}

export interface SSETextEnd {
  type: "text_end";
  messageId: string;
}

export interface SSEReasoningStart {
  type: "reasoning_start";
  messageId: string;
}

export interface SSEReasoningDelta {
  type: "reasoning_delta";
  messageId: string;
  delta: string;
}

export interface SSEReasoningEnd {
  type: "reasoning_end";
  messageId: string;
}

export interface SSEToolCallStart {
  type: "tool_call_start";
  messageId: string;
  toolCallId: string;
  toolName: string;
}

export interface SSEToolCallDelta {
  type: "tool_call_delta";
  toolCallId: string;
  delta: string; // JSON fragment
}

export interface SSEToolCallEnd {
  type: "tool_call_end";
  toolCallId: string;
}

export interface SSEToolResult {
  type: "tool_result";
  toolCallId: string;
  result: unknown;
  error: string | null;
}

export interface SSEDone {
  type: "done";
  messageId: string;
}

export interface SSEError {
  type: "error";
  message: string;
  code: string;
}

export interface SSEPong {
  type: "pong";
}

export interface SSEActivity {
  type: "activity";
  kind: "searching" | "retrieved" | "analyzing" | "answering" | "rewriting";
  message: string;
}

/** Union of all SSE event types received from the backend. */
export type SSEServerMessage =
  | SSETextStart
  | SSETextDelta
  | SSETextEnd
  | SSEReasoningStart
  | SSEReasoningDelta
  | SSEReasoningEnd
  | SSEToolCallStart
  | SSEToolCallDelta
  | SSEToolCallEnd
  | SSEToolResult
  | SSEDone
  | SSEError
  | SSEPong
  | SSEActivity;
