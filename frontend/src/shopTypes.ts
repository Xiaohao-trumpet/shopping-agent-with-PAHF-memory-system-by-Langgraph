export interface ProductVariant {
  sku_code: string;
  attributes: Record<string, string>;
  price: number;
  stock: number;
  in_stock: boolean;
}

export interface Product {
  product_id: string;
  title: string;
  description: string;
  category: string;
  brand: string;
  price: number;
  currency: string;
  rating: number;
  rating_count: number;
  image_url: string;
  attributes: Record<string, string>;
  in_stock?: boolean;
  variants?: ProductVariant[];
}

export type ConvStatus = "bot" | "queued" | "human" | "resolved";
export type MsgRole = "customer" | "ai" | "agent" | "system";

export interface ConvMessage {
  id: number;
  conversation_id: string;
  role: MsgRole;
  sender: string;
  content: string;
  meta: Record<string, unknown>;
  created_at: number;
}

export interface Conversation {
  conversation_id: string;
  customer_id: string;
  channel: string;
  status: ConvStatus;
  assigned_agent?: string | null;
  priority: number;
  escalation_reason?: string | null;
  csat?: number | null;
  created_at: number;
  updated_at: number;
  last_message_at: number;
}

export interface OrderLite {
  order_id: string;
  status: string;
  total: number;
  currency: string;
  created_at: number;
  items?: Array<{ title: string; qty: number; unit_price: number }>;
}

export interface CustomerContext {
  customer_id: string;
  orders: OrderLite[];
  memories: Array<{ id: number; text: string }>;
}

export interface AgentContext {
  conversation: Conversation;
  messages: ConvMessage[];
  customer: CustomerContext;
}

export interface BusEvent {
  type: string;
  [key: string]: unknown;
}

// ------------------------------------------------------- review analytics
export type Sentiment = "positive" | "neutral" | "negative";

export interface ProductReview {
  review_id: string;
  product_id: string;
  author_name: string;
  rating: number;
  title: string;
  content: string;
  tags: string[];
  aspects: string[];
  sentiment: Sentiment;
  source: "user" | "ai" | "seed";
  helpful: number;
  created_at: number;
}

export interface ReviewStats {
  count: number;
  avg_rating: number;
  distribution: Record<string, number>;
  sentiment: Record<string, number>;
  positive_share: number;
  negative_share: number;
  recent_avg: number;
  baseline_avg: number;
  recent_count: number;
  recent_share: number;
  rating_trend: number;
  top_tags: Array<{ tag: string; count: number }>;
  top_aspects: Array<{ aspect: string; count: number }>;
  trend: Array<{ period: string; count: number; avg: number | null }>;
}

export interface PotentialTier {
  key: "star" | "rising" | "stable" | "at_risk" | "unrated";
  label: string;
  advice: string;
}

export interface PotentialDriver {
  key: string;
  label: string;
  value: number;
  weight: number;
  contribution: number;
  tone: Sentiment;
  reason: string;
}

export interface ProductPotential {
  score: number;
  tier: PotentialTier;
  components: Record<string, number>;
  drivers?: PotentialDriver[];
  confidence: string;
}

export interface ProductAnalyticsRow {
  product_id: string;
  title: string;
  category: string;
  brand: string;
  price: number;
  image_url: string;
  review_count: number;
  avg_rating: number;
  positive_share: number;
  negative_share: number;
  rating_trend: number;
  recent_share: number;
  score: number;
  tier: PotentialTier;
  components: Record<string, number>;
  confidence: string;
  demand: { units: number; orders: number; revenue: number };
}

export interface AIInsight {
  generated_by: "ai" | "heuristic";
  summary: string;
  pros: string[];
  cons: string[];
  themes: Array<{ aspect: string; sentiment: Sentiment }>;
  potential_narrative: string;
  recommended_actions: string[];
  risk_level: "low" | "medium" | "high";
}

export interface ProductAnalyticsDetail {
  product: {
    product_id: string;
    title: string;
    category: string;
    brand: string;
    price: number;
    image_url: string;
    description: string;
  };
  potential: ProductPotential;
  stats: ReviewStats;
  demand: { units: number; orders: number; revenue: number };
  reviews: ProductReview[];
  insight: AIInsight;
}

export interface StoreExecSummary {
  generated_by: "ai" | "heuristic";
  headline: string;
  highlights: string[];
  concerns: string[];
  opportunities: string[];
  strategic_actions: string[];
}

export interface StorePotential {
  score: number;
  tier: PotentialTier;
  rated_products: number;
  total_products: number;
  tier_counts: Record<string, number>;
  avg_rating: number;
  total_reviews: number;
  positive_share: number;
  negative_share: number;
  rating_trend: number;
  recent_share: number;
  categories: Array<{
    category: string;
    avg_score: number;
    avg_rating: number;
    products: number;
    reviews: number;
  }>;
  top_products: Array<{ product_id: string; title: string; score: number; tier: PotentialTier; avg_rating: number }>;
  watch_products: Array<{ product_id: string; title: string; score: number; tier: PotentialTier; avg_rating: number }>;
}

export interface StoreAnalytics {
  generated_at: number;
  store: StorePotential;
  stats: ReviewStats;
  summary: StoreExecSummary;
}
