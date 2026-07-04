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
  OrderLite,
  Shipment,
  Cart,
} from "./shopTypes";

const DEFAULT_API_BASE = import.meta.env.PROD ? "/server" : "http://localhost:8000";
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE).replace(/\/$/, "");
const WS_BASE = API_BASE.startsWith("http")
  ? API_BASE.replace(/^http/, "ws")
  : `${typeof window === "undefined" ? "" : window.location.protocol === "https:" ? "wss" : "ws"}://${
      typeof window === "undefined" ? "" : window.location.host
    }${API_BASE}`;

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

export async function fetchCustomerOrders(customerId: string, limit = 10): Promise<OrderLite[]> {
  const qs = new URLSearchParams({ customer_id: customerId, limit: String(limit) });
  const res = await request<{ orders: OrderLite[] }>(`/api/v1/shop/orders?${qs.toString()}`);
  return res.orders ?? [];
}

export async function fetchOrderDetail(orderId: string, customerId?: string): Promise<OrderLite> {
  const qs = new URLSearchParams();
  if (customerId) qs.set("customer_id", customerId);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request(`/api/v1/shop/orders/${encodeURIComponent(orderId)}${suffix}`);
}

export async function fetchOrderShipment(orderId: string, customerId?: string): Promise<Shipment> {
  const qs = new URLSearchParams();
  if (customerId) qs.set("customer_id", customerId);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request(`/api/v1/shop/orders/${encodeURIComponent(orderId)}/shipment${suffix}`);
}

export async function fetchCart(customerId: string): Promise<Cart> {
  const qs = new URLSearchParams({ customer_id: customerId });
  return request(`/api/v1/shop/cart?${qs.toString()}`);
}

export async function addCartItem(payload: {
  customerId: string;
  productId: string;
  skuCode?: string;
  qty?: number;
}): Promise<Cart> {
  return request("/api/v1/shop/cart/items", {
    method: "POST",
    body: JSON.stringify({
      customer_id: payload.customerId,
      product_id: payload.productId,
      sku_code: payload.skuCode ?? "",
      qty: payload.qty ?? 1,
    }),
  });
}

export async function updateCartItem(payload: {
  customerId: string;
  skuCode: string;
  qty: number;
}): Promise<Cart> {
  return request("/api/v1/shop/cart/items", {
    method: "PUT",
    body: JSON.stringify({
      customer_id: payload.customerId,
      sku_code: payload.skuCode,
      qty: payload.qty,
    }),
  });
}

export async function clearCart(customerId: string): Promise<Cart> {
  const qs = new URLSearchParams({ customer_id: customerId });
  return request(`/api/v1/shop/cart?${qs.toString()}`, { method: "DELETE" });
}

export async function checkoutCart(payload: {
  customerId: string;
  shippingAddress?: string;
  shippingMethod?: string;
}): Promise<OrderLite> {
  return request("/api/v1/shop/cart/checkout", {
    method: "POST",
    body: JSON.stringify({
      customer_id: payload.customerId,
      shipping_address: payload.shippingAddress ?? "",
      shipping_method: payload.shippingMethod ?? "待选择",
    }),
  });
}

export async function createReturnRequest(payload: {
  customerId: string;
  orderId: string;
  skuCode?: string;
  reason: string;
}): Promise<{ created: boolean; return_id: string; order_id: string; status: string; refund_amount: number }> {
  return request("/api/v1/shop/returns", {
    method: "POST",
    body: JSON.stringify({
      customer_id: payload.customerId,
      order_id: payload.orderId,
      sku_code: payload.skuCode,
      reason: payload.reason,
    }),
  });
}

// ------------------------------------------------------------- agent console
// All of these require an admin session token -- the agent console exposes
// customer chat content and PAHF memory dumps, so it's gated the same as
// the rest of the backoffice.
export async function fetchAgentConversations(token: string, status = "all"): Promise<Conversation[]> {
  const res = await request<{ conversations: Conversation[] }>(
    `/api/v1/agent/conversations?status=${encodeURIComponent(status)}`,
    { headers: authHeaders(token) }
  );
  return res.conversations ?? [];
}

export async function fetchAgentContext(token: string, conversationId: string): Promise<AgentContext> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}`, {
    headers: authHeaders(token),
  });
}

export async function claimConversation(
  token: string,
  conversationId: string,
  agentId: string,
  agentName: string
): Promise<Conversation> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/claim`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ agent_id: agentId, agent_name: agentName }),
  });
}

export async function sendAgentMessage(
  token: string,
  conversationId: string,
  agentId: string,
  content: string
): Promise<ConvMessage> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/message`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ agent_id: agentId, content }),
  });
}

export async function releaseConversation(
  token: string,
  conversationId: string,
  agentId: string
): Promise<Conversation> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/release`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ agent_id: agentId }),
  });
}

export async function resolveConversation(
  token: string,
  conversationId: string,
  agentId: string,
  csat?: number
): Promise<Conversation> {
  return request(`/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/resolve`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ agent_id: agentId, csat }),
  });
}

export async function suggestReply(token: string, conversationId: string): Promise<string> {
  const res = await request<{ suggestion: string }>(
    `/api/v1/agent/conversations/${encodeURIComponent(conversationId)}/suggest`,
    { headers: authHeaders(token) }
  );
  return res.suggestion ?? "";
}

export async function fetchAgentStats(token: string): Promise<{
  counts: Record<string, number>;
  online_agents: number;
}> {
  return request("/api/v1/agent/stats", { headers: authHeaders(token) });
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
  account_type?: "admin" | "customer";
  email?: string;
  phone?: string;
}

export interface CustomerUser {
  customer_id: string;
  name: string;
  email: string;
  phone: string;
  tier: string;
  created_at: number;
}

export interface CustomerLoginResponse {
  customer: CustomerUser;
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

export async function loginCustomer(customerId: string, password: string): Promise<CustomerLoginResponse> {
  return request("/api/v1/auth/customer-login", {
    method: "POST",
    body: JSON.stringify({ customer_id: customerId, password }),
  });
}

export async function registerCustomer(payload: {
  customerId: string;
  password: string;
  name: string;
  email?: string;
  phone?: string;
}): Promise<CustomerLoginResponse> {
  return request("/api/v1/auth/customer-register", {
    method: "POST",
    body: JSON.stringify({
      customer_id: payload.customerId,
      password: payload.password,
      name: payload.name,
      email: payload.email ?? "",
      phone: payload.phone ?? "",
    }),
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

// ------------------------------------------------------- admin memory management
export interface MemoryCustomer {
  person_id: string;
  memory_count: number;
  profile: CustomerUser | null;
}

export interface CustomerMemoryEntry {
  id: number;
  text: string;
}

export async function fetchMemoryCustomers(token: string): Promise<MemoryCustomer[]> {
  const res = await request<{ customers: MemoryCustomer[] }>("/api/v1/admin/memory/customers", {
    headers: authHeaders(token),
  });
  return res.customers ?? [];
}

export async function fetchCustomerMemories(token: string, personId: string): Promise<CustomerMemoryEntry[]> {
  const res = await request<{ memories: CustomerMemoryEntry[] }>(
    `/api/v1/admin/memory/customers/${encodeURIComponent(personId)}/memories`,
    { headers: authHeaders(token) }
  );
  return res.memories ?? [];
}

export async function deleteCustomerMemory(token: string, personId: string, memoryId: number): Promise<void> {
  await request(`/api/v1/admin/memory/customers/${encodeURIComponent(personId)}/memories/${memoryId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

// ------------------------------------------------------------------- sockets
export function customerSocketUrl(customerId: string): string {
  return `${WS_BASE}/ws/customer/${encodeURIComponent(customerId)}`;
}
export function agentSocketUrl(agentId: string, token: string): string {
  return `${WS_BASE}/ws/agent/${encodeURIComponent(agentId)}?token=${encodeURIComponent(token)}`;
}
export function conversationSocketUrl(conversationId: string, token: string): string {
  return `${WS_BASE}/ws/conversation/${encodeURIComponent(conversationId)}?token=${encodeURIComponent(token)}`;
}
