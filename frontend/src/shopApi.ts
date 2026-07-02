import type {
  Product,
  Conversation,
  ConvMessage,
  AgentContext,
  ProductReview,
  ReviewStats,
  ProductAnalyticsRow,
  ProductAnalyticsDetail,
  StoreAnalytics,
  AIInsight,
  ProductPotential,
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

// --------------------------------------------------------- product reviews
export async function fetchReviewTags(): Promise<string[]> {
  const res = await request<{ tags: string[] }>("/api/v1/shop/review-tags");
  return res.tags ?? [];
}

export async function fetchProductReviews(
  productId: string,
  limit = 20,
  sentiment = ""
): Promise<{ stats: ReviewStats; reviews: ProductReview[] }> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (sentiment) qs.set("sentiment", sentiment);
  return request(`/api/v1/shop/products/${encodeURIComponent(productId)}/reviews?${qs.toString()}`);
}

export async function submitProductReview(payload: {
  productId: string;
  customerId: string;
  rating: number;
  title: string;
  content: string;
  tags: string[];
}): Promise<{ review_id: string }> {
  return request(`/api/v1/shop/products/${encodeURIComponent(payload.productId)}/reviews`, {
    method: "POST",
    body: JSON.stringify({
      customer_id: payload.customerId,
      rating: payload.rating,
      title: payload.title,
      content: payload.content,
      tags: payload.tags,
    }),
  });
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

// ---------------------------------------------------------------- backoffice
export interface AdminUser {
  username: string;
  role: string;
  display_name: string;
  created_at: number;
  last_login_at?: number | null;
}

export interface AdminLoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: number;
  user: AdminUser;
}

export interface AdminProduct extends Product {
  sku_count: number;
  stock_total: number;
  status: string;
}

export interface AdminRating {
  conversation_id: string;
  customer_id: string;
  stars: number;
  tags: string[];
  comment: string;
  created_at: number;
}

export interface AdminOverview {
  generated_at: number;
  admin: AdminUser;
  catalog: {
    products: number;
    active_products: number;
    skus: number;
    customers: number;
    orders: number;
    coupons: number;
    return_requests: number;
    total_stock: number;
    low_stock_skus: number;
    revenue: number;
    orders_by_status: Record<string, number>;
    categories: Array<{ category: string; count: number }>;
  };
  conversations: {
    total: number;
    by_status: Record<string, number>;
    latest: Conversation[];
  };
  feedback: FeedbackSummary;
  agents: {
    online_agents: number;
    agents: Array<Record<string, unknown>>;
  };
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export async function loginAdmin(username: string, password: string): Promise<AdminLoginResponse> {
  return request("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function fetchAdminMe(token: string): Promise<{ user: AdminUser }> {
  return request("/api/v1/auth/me", { headers: authHeaders(token) });
}

export async function logoutAdmin(token: string): Promise<void> {
  await request("/api/v1/auth/logout", { method: "POST", headers: authHeaders(token) });
}

export async function fetchAdminOverview(token: string): Promise<AdminOverview> {
  return request("/api/v1/admin/overview", { headers: authHeaders(token) });
}

export async function fetchAdminConversations(token: string, status = "all"): Promise<Conversation[]> {
  const res = await request<{ conversations: Conversation[] }>(
    `/api/v1/admin/conversations?status=${encodeURIComponent(status)}`,
    { headers: authHeaders(token) }
  );
  return res.conversations ?? [];
}

export async function fetchAdminProducts(token: string): Promise<AdminProduct[]> {
  const res = await request<{ products: AdminProduct[] }>("/api/v1/admin/products?limit=200", {
    headers: authHeaders(token),
  });
  return res.products ?? [];
}

export async function fetchAdminRatings(token: string): Promise<AdminRating[]> {
  const res = await request<{ ratings: AdminRating[] }>("/api/v1/admin/feedback/ratings?limit=200", {
    headers: authHeaders(token),
  });
  return res.ratings ?? [];
}

export async function fetchAdminUsers(token: string): Promise<AdminUser[]> {
  const res = await request<{ users: AdminUser[] }>("/api/v1/admin/users", {
    headers: authHeaders(token),
  });
  return res.users ?? [];
}

// -------------------------------------------------- admin review analytics
export async function fetchStoreAnalytics(token: string, ai = false): Promise<StoreAnalytics> {
  return request(`/api/v1/admin/analytics/store?ai=${ai ? 1 : 0}`, { headers: authHeaders(token) });
}

export async function fetchProductAnalytics(
  token: string,
  opts: { sort?: string; order?: string; category?: string; limit?: number } = {}
): Promise<ProductAnalyticsRow[]> {
  const qs = new URLSearchParams();
  qs.set("sort", opts.sort ?? "score");
  qs.set("order", opts.order ?? "desc");
  if (opts.category) qs.set("category", opts.category);
  qs.set("limit", String(opts.limit ?? 200));
  const res = await request<{ products: ProductAnalyticsRow[] }>(
    `/api/v1/admin/analytics/products?${qs.toString()}`,
    { headers: authHeaders(token) }
  );
  return res.products ?? [];
}

export async function fetchProductAnalyticsDetail(
  token: string,
  productId: string,
  ai = false
): Promise<ProductAnalyticsDetail> {
  return request(`/api/v1/admin/analytics/products/${encodeURIComponent(productId)}?ai=${ai ? 1 : 0}`, {
    headers: authHeaders(token),
  });
}

export async function requestProductAiInsight(
  token: string,
  productId: string
): Promise<{ insight: AIInsight; potential: ProductPotential }> {
  return request(`/api/v1/admin/analytics/products/${encodeURIComponent(productId)}/ai-insight`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export async function generateProductReviews(
  token: string,
  productId: string,
  n = 5,
  skew: "positive" | "mixed" | "critical" = "mixed"
): Promise<{ generated_by: string; persisted: number; reviews: Array<{ rating: number; title: string; content: string }> }> {
  return request(`/api/v1/admin/analytics/products/${encodeURIComponent(productId)}/generate-reviews`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ n, skew, persist: true }),
  });
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
