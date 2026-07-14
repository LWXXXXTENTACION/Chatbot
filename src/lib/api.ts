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
};
