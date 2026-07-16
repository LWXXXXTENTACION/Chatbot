/**
 * Centralized API client.
 * All API calls go through fetchWithAuth for automatic token handling.
 */

import { fetchWithAuth } from "./auth";

const BACKEND = ""; // Same-origin, Next.js handles routing

export interface UserInfo {
  id: string;
  username: string;
  created_at: string;
}

export interface TokenData {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: UserInfo;
}

export interface ConversationData {
  id: string;
  title: string;
  model: string;
  message_count?: number;
  created_at: string;
  updated_at: string;
  messages?: MessageData[];
}

export interface MessageData {
  id: string;
  role: string;
  created_at: string;
  parts: MessagePartData[];
}

export interface MessagePartData {
  id: string;
  type: string;
  text: string | null;
  tool_call_id: string | null;
  tool_state: string | null;
  tool_input: unknown;
  tool_output: unknown;
  tool_error: string | null;
  position: number;
}

export interface TraceMetrics {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  llm_calls: number;
  tool_calls: number;
  tool_errors: number;
  cache_hits: number;
  sources: number;
}

export interface TraceTimelineEvent {
  id: string;
  type: string;
  label: string;
  status: "running" | "completed" | "error";
  at_ms: number;
  metadata: Record<string, unknown>;
}

export interface RunEvaluation {
  passed: boolean;
  note: string;
  case_id: string;
  updated_at: string;
}

export interface RunTrace {
  schema_version: number;
  run_id: string;
  version: {
    id: string;
    label: string;
    code_fingerprint: string;
  };
  conversation_id: string;
  user_message_id: string;
  conversation?: { id: string; title: string };
  model: string;
  search_mode: "auto" | "web" | "deep";
  status: "success" | "error" | "cancelled";
  error_code: string | null;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  metrics: TraceMetrics;
  context: {
    strategies?: string[];
    estimated_tokens_before?: number;
    estimated_tokens_after?: number;
    max_tokens?: number;
    removed_messages?: number;
    compacted_tool_results?: number;
    overflowed?: boolean;
  };
  timeline: TraceTimelineEvent[];
  evaluation: RunEvaluation | null;
}

export interface VersionCategoryMetrics {
  id: string;
  label: string;
  runs: number;
  total_tokens: number;
  avg_tokens: number;
}

export interface ObservabilityVersion {
  id: string;
  label: string;
  model: string;
  code_fingerprint: string;
  first_seen: string;
  last_seen: string;
  runs: number;
  successful_runs: number;
  failed_runs: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  avg_tokens: number;
  llm_calls: number;
  tool_calls: number;
  avg_duration_ms: number;
  evaluated_runs: number;
  passed_runs: number;
  success_rate: number;
  pass_rate: number | null;
  categories: VersionCategoryMetrics[];
}

export interface ObservabilityOverview {
  versions: ObservabilityVersion[];
  runs: RunTrace[];
}

// ——— Auth ———

export const api = {
  async register(
    username: string,
    password: string,
  ): Promise<TokenData> {
    const res = await fetch(`${BACKEND}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "注册失败");
    }
    return res.json();
  },

  async login(username: string, password: string): Promise<TokenData> {
    const res = await fetch(`${BACKEND}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "登录失败");
    }
    return res.json();
  },

  async me(): Promise<UserInfo> {
    const res = await fetchWithAuth(`${BACKEND}/api/auth/me`);
    if (!res.ok) throw new Error("获取用户信息失败");
    return res.json();
  },

  async refresh(refreshToken: string): Promise<TokenData> {
    const res = await fetch(`${BACKEND}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) throw new Error("刷新令牌失败");
    return res.json();
  },

  async logout(): Promise<void> {
    await fetchWithAuth(`${BACKEND}/api/auth/logout`, { method: "POST" });
  },

  // ——— Conversations ———

  async listConversations(): Promise<ConversationData[]> {
    const res = await fetchWithAuth(`${BACKEND}/api/conversations`);
    if (!res.ok) throw new Error("获取对话列表失败");
    return res.json();
  },

  async getConversation(id: string): Promise<ConversationData> {
    const res = await fetchWithAuth(`${BACKEND}/api/conversations/${id}`);
    if (!res.ok) throw new Error("获取对话失败");
    return res.json();
  },

  async createConversation(body?: {
    title?: string;
    model?: string;
  }): Promise<ConversationData> {
    const res = await fetchWithAuth(`${BACKEND}/api/conversations`, {
      method: "POST",
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error("创建对话失败");
    return res.json();
  },

  async updateConversation(
    id: string,
    body: { title?: string; model?: string },
  ): Promise<ConversationData> {
    const res = await fetchWithAuth(`${BACKEND}/api/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("更新对话失败");
    return res.json();
  },

  async deleteConversation(id: string): Promise<void> {
    const res = await fetchWithAuth(`${BACKEND}/api/conversations/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("删除对话失败");
  },

  // ——— Observability ———

  async getObservabilityOverview(limit = 200): Promise<ObservabilityOverview> {
    const res = await fetchWithAuth(
      `${BACKEND}/api/observability/overview?limit=${limit}`,
    );
    if (!res.ok) throw new Error("获取运行观测数据失败");
    return res.json();
  },

  async evaluateRun(
    runId: string,
    body: { passed: boolean | null; note?: string; case_id?: string },
  ): Promise<RunTrace> {
    const res = await fetchWithAuth(
      `${BACKEND}/api/observability/runs/${runId}/evaluation`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    );
    if (!res.ok) throw new Error("保存评测结果失败");
    return res.json();
  },
};
