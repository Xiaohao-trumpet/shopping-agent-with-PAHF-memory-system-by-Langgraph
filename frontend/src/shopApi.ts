import type {
  Product,
  Conversation,
  ConvMessage,
  AgentContext,
} from "./shopTypes";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const WS_BASE = API_BASE.replace(/^http/, "ws");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }
  return (await response.json()) as T;
}

// ---------------------------------------------------------------- storefront
export async function fetchCategories(): Promise<string[]> {
  const res = await request<{ categories: string[] }>("/api/v1/shop/categories");
  return res.categories ?? [];
}

export async function fetchProducts(params: {
  query?: string;
  category?: string;
  maxPrice?: number;
  limit?: number;
}): Promise<Product[]> {
  const qs = new URLSearchParams();
  if (params.query) qs.set("query", params.query);
  if (params.category) qs.set("category", params.category);
  if (params.maxPrice != null) qs.set("max_price", String(params.maxPrice));
  qs.set("limit", String(params.limit ?? 24));
  const res = await request<{ products: Product[] }>(`/api/v1/shop/products?${qs.toString()}`);
  return res.products ?? [];
}

export async function fetchProductDetail(productId: string): Promise<Product> {
  return request<Product>(`/api/v1/shop/products/${encodeURIComponent(productId)}`);
}

export async function fetchConversation(
  customerId: string
): Promise<{ conversation: Conversation; messages: ConvMessage[] }> {
  return request(`/api/v1/shop/conversation/${encodeURIComponent(customerId)}`);
}

// ------------------------------------------------------------- agent console
export async function fetchAgentConversations(status = "all"): Promise<Conversation[]> {
  const res = await request<{ conversations: Conversation[] }>(
    `/api/v1/agent/conversations?status=${encodeURIComponent(status)}`
  );
  return res.conversations ?? [];
}

export async function fetchAgentContext(conversationId: string): Promise<AgentContext> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}`);
}

export async function claimConversation(
  conversationId: string,
  agentId: string,
  agentName: string
): Promise<Conversation> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/claim`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, agent_name: agentName }),
  });
}

export async function sendAgentMessage(
  conversationId: string,
  agentId: string,
  content: string
): Promise<ConvMessage> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/message`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, content }),
  });
}

export async function releaseConversation(conversationId: string, agentId: string): Promise<Conversation> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/release`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId }),
  });
}

export async function resolveConversation(
  conversationId: string,
  agentId: string,
  csat?: number
): Promise<Conversation> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, csat }),
  });
}

export async function suggestReply(conversationId: string): Promise<string> {
  const res = await request<{ suggestion: string }>(
    `/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/suggest`
  );
  return res.suggestion ?? "";
}

export async function fetchAgentStats(): Promise<{
  counts: Record<string, number>;
  online_agents: number;
}> {
  return request("/api/v1/agent/stats");
}

// ----------------------------------------------------------------- feedback
export async function fetchFeedbackTags(): Promise<string[]> {
  const res = await request<{ tags: string[] }>("/api/v1/feedback/tags");
  return res.tags ?? [];
}

export async function sendMessageFeedback(
  conversationId: string,
  messageId: number,
  customerId: string,
  value: "up" | "down"
): Promise<void> {
  await request("/api/v1/feedback/message", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: conversationId,
      message_id: messageId,
      customer_id: customerId,
      value,
    }),
  });
}

export async function sendRating(payload: {
  conversationId: string;
  customerId: string;
  stars: number;
  tags: string[];
  comment: string;
}): Promise<void> {
  await request("/api/v1/feedback/rating", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      customer_id: payload.customerId,
      stars: payload.stars,
      tags: payload.tags,
      comment: payload.comment,
    }),
  });
}

export async function endConversation(customerId: string): Promise<void> {
  await request("/api/v1/shop/end", {
    method: "POST",
    body: JSON.stringify({ customer_id: customerId }),
  });
}

export interface FeedbackSummary {
  ratings: { count: number; avg_stars: number; distribution: Record<string, number> };
  messages: { up: number; down: number; total: number; satisfaction: number | null };
  top_tags: Array<{ tag: string; count: number }>;
}

export async function fetchFeedbackSummary(): Promise<FeedbackSummary> {
  return request("/api/v1/feedback/summary");
}

// ------------------------------------------------------------------- sockets
export function customerSocketUrl(customerId: string): string {
  return `${WS_BASE}/ws/customer/${encodeURIComponent(customerId)}`;
}
export function agentSocketUrl(agentId: string): string {
  return `${WS_BASE}/ws/agent/${encodeURIComponent(agentId)}`;
}
export function conversationSocketUrl(conversationId: string): string {
  return `${WS_BASE}/ws/conversation/${encodeURIComponent(conversationId)}`;
}
