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
