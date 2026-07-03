import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  Product,
  ConvMessage,
  ConvStatus,
  BusEvent,
  ProductReview,
  ReviewStats,
  OrderLite,
  Shipment,
} from "./shopTypes";
import {
  fetchCategories,
  fetchProducts,
  fetchProductDetail,
  fetchCustomerOrders,
  fetchOrderShipment,
  createReturnRequest,
  customerSocketUrl,
  fetchFeedbackTags,
  sendMessageFeedback,
  sendRating,
  endConversation,
  fetchProductReviews,
  submitProductReview,
  fetchReviewTags,
} from "./shopApi";

const CATEGORY_EMOJI: Record<string, string> = {
  "数码3C": "📱",
  家居: "🛋️",
  家居日用: "🏠",
  服饰: "👕",
  服饰鞋包: "👟",
  美妆个护: "💄",
  母婴宠物: "🧸",
  食品饮料: "☕",
  运动户外: "🏕️",
  图书文具: "📚",
};

function emojiFor(category: string): string {
  return CATEGORY_EMOJI[category] ?? "🛒";
}

function yuan(n: number): string {
  return `¥${n.toFixed(0)}`;
}

function timeAgo(ts: number): string {
  const days = Math.max(0, Math.floor((Date.now() / 1000 - ts) / 86400));
  if (days === 0) return "今天";
  if (days < 30) return `${days} 天前`;
  return `${Math.floor(days / 30)} 个月前`;
}

// -------------------------------------------------------- product reviews
function ProductReviews({ productId, customerId }: { productId: string; customerId: string }) {
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [reviews, setReviews] = useState<ProductReview[]>([]);
  const [filter, setFilter] = useState("");
  const [writing, setWriting] = useState(false);
  const [tagOptions, setTagOptions] = useState<string[]>([]);
  const [rating, setRating] = useState(5);
  const [hover, setHover] = useState(0);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetchProductReviews(productId, 30, filter)
      .then((r) => {
        setStats(r.stats);
        setReviews(r.reviews);
      })
      .catch(() => undefined);
  }, [productId, filter]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    fetchReviewTags().then(setTagOptions).catch(() => setTagOptions([]));
  }, []);

  const toggleTag = (t: string) =>
    setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));

  const submit = async () => {
    if (!content.trim()) return;
    setBusy(true);
    try {
      await submitProductReview({ productId, customerId, rating, title, content, tags });
      setWriting(false);
      setTitle("");
      setContent("");
      setTags([]);
      setRating(5);
      load();
    } finally {
      setBusy(false);
    }
  };

  const total = stats?.count ?? 0;
  const trendUp = (stats?.rating_trend ?? 0) > 0.1;
  const trendDown = (stats?.rating_trend ?? 0) < -0.1;

  return (
    <div className="pr">
      <div className="pr-head">
        <h4>用户评价 {total > 0 && <small>（{total} 条）</small>}</h4>
        <button className="pr-write-btn" onClick={() => setWriting((w) => !w)}>
          {writing ? "取消" : "✍️ 写评价"}
        </button>
      </div>

      {total > 0 && stats && (
        <div className="pr-summary">
          <div className="pr-score">
            <strong>{stats.avg_rating.toFixed(1)}</strong>
            <span className="pr-score-stars">
              {"★★★★★".slice(0, Math.round(stats.avg_rating))}
            </span>
            <small>
              好评 {Math.round(stats.positive_share * 100)}%
              {trendUp && <b className="up"> ↑近期升</b>}
              {trendDown && <b className="down"> ↓近期降</b>}
            </small>
          </div>
          <div className="pr-dist">
            {[5, 4, 3, 2, 1].map((s) => {
              const c = stats.distribution[String(s)] ?? 0;
              const pct = total ? Math.round((c / total) * 100) : 0;
              return (
                <div key={s} className="pr-dist-row">
                  <span>{s}★</span>
                  <i>
                    <b style={{ width: `${pct}%` }} />
                  </i>
                  <em>{c}</em>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {stats && stats.top_tags.length > 0 && (
        <div className="pr-filter">
          <button className={filter === "" ? "chip active" : "chip"} onClick={() => setFilter("")}>
            全部
          </button>
          <button
            className={filter === "positive" ? "chip active" : "chip"}
            onClick={() => setFilter("positive")}
          >
            好评
          </button>
          <button
            className={filter === "negative" ? "chip active" : "chip"}
            onClick={() => setFilter("negative")}
          >
            差评
          </button>
          {stats.top_tags.slice(0, 5).map((t) => (
            <span key={t.tag} className="pr-tagchip">
              {t.tag} {t.count}
            </span>
          ))}
        </div>
      )}

      {writing && (
        <div className="pr-form">
          <div className="stars sm">
            {[1, 2, 3, 4, 5].map((n) => (
              <span
                key={n}
                className={`star ${(hover || rating) >= n ? "on" : ""}`}
                onMouseEnter={() => setHover(n)}
                onMouseLeave={() => setHover(0)}
                onClick={() => setRating(n)}
              >
                ★
              </span>
            ))}
          </div>
          <input
            className="pr-input"
            placeholder="标题（选填）"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            className="pr-textarea"
            placeholder="说说这款商品的使用体验…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
          <div className="tag-row">
            {tagOptions.map((t) => (
              <button
                key={t}
                className={tags.includes(t) ? "tag on" : "tag"}
                onClick={() => toggleTag(t)}
              >
                {t}
              </button>
            ))}
          </div>
          <button className="primary wide" disabled={busy || !content.trim()} onClick={submit}>
            {busy ? "提交中…" : "提交评价"}
          </button>
        </div>
      )}

      <div className="pr-list">
        {reviews.length === 0 && <p className="muted">暂无评价，快来第一个评价吧～</p>}
        {reviews.map((r) => (
          <div key={r.review_id} className="pr-item">
            <div className="pr-item-head">
              <span className="pr-author">{r.author_name}</span>
              <span className={`pr-item-stars s${r.rating}`}>
                {"★★★★★".slice(0, r.rating)}
              </span>
              {r.source === "ai" && <span className="pr-badge ai">AI</span>}
              <span className="pr-time">{timeAgo(r.created_at)}</span>
            </div>
            {r.title && <div className="pr-item-title">{r.title}</div>}
            <div className="pr-item-body">{r.content}</div>
            {r.tags.length > 0 && (
              <div className="pr-item-tags">
                {r.tags.map((t, i) => (
                  <span key={i}>{t}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function timeText(ts?: number | null): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function orderStatusText(status: string): string {
  const map: Record<string, string> = {
    shipped: "运输中",
    delivered: "已签收",
    pending_payment: "待付款",
    paid: "已付款",
    cancelled: "已取消",
  };
  return map[status] ?? status;
}

function visualKind(product: Product): string {
  const text = `${product.title} ${product.category}`.toLowerCase();
  if (text.includes("耳机") || text.includes("音")) return "earbuds";
  if (text.includes("笔记本") || text.includes("显示器")) return "screen";
  if (text.includes("手机")) return "phone";
  if (text.includes("鞋") || text.includes("服") || text.includes("裤") || text.includes("衬衫")) return "apparel";
  if (text.includes("锅") || text.includes("水壶") || text.includes("椅") || text.includes("家居")) return "home";
  if (text.includes("面膜") || text.includes("精华") || text.includes("防晒") || text.includes("牙刷")) return "beauty";
  if (text.includes("猫") || text.includes("婴") || text.includes("奶")) return "baby";
  if (text.includes("咖啡") || text.includes("茶") || text.includes("坚果") || text.includes("燕麦")) return "food";
  if (text.includes("帐篷") || text.includes("瑜伽") || text.includes("哑铃") || text.includes("骑行")) return "outdoor";
  return "desk";
}

function isUsableImage(url?: string): boolean {
  return Boolean(url && (url.startsWith("/") || /^https?:\/\//.test(url)) && !url.includes("img.shop.local"));
}

function ProductVisual({ product, large = false }: { product: Product; large?: boolean }) {
  const [imageFailed, setImageFailed] = useState(false);
  if (isUsableImage(product.image_url) && !imageFailed) {
    return (
      <div className={large ? "product-photo big" : "product-photo"}>
        <img src={product.image_url} alt={product.title} onError={() => setImageFailed(true)} />
      </div>
    );
  }
  return (
    <div className={`product-photo local ${large ? "big" : ""} kind-${visualKind(product)}`}>
      <div className="photo-backdrop" />
      <div className="photo-object">
        <span className="photo-main" />
        <span className="photo-accent" />
        <span className="photo-detail" />
      </div>
      <div className="photo-caption">{product.brand}</div>
    </div>
  );
}

// ---------------------------------------------------------------- chat hook
interface ChatState {
  messages: ConvMessage[];
  status: ConvStatus;
  connected: boolean;
  conversationId: string;
}

function useCustomerChat(customerId: string, enabled: boolean) {
  const [state, setState] = useState<ChatState>({
    messages: [],
    status: "bot",
    connected: false,
    conversationId: "",
  });
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!enabled || !customerId) return;
    const ws = new WebSocket(customerSocketUrl(customerId));
    wsRef.current = ws;

    ws.onopen = () => setState((s) => ({ ...s, connected: true }));
    ws.onclose = () => setState((s) => ({ ...s, connected: false }));
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data) as BusEvent;
      if (data.type === "history") {
        const conv = data.conversation as { status: ConvStatus; conversation_id: string };
        setState({
          messages: (data.messages as ConvMessage[]) ?? [],
          status: conv?.status ?? "bot",
          connected: true,
          conversationId: conv?.conversation_id ?? "",
        });
      } else if (data.type === "message") {
        const msg = data.message as ConvMessage;
        setState((s) => {
          if (s.messages.some((m) => m.id === msg.id)) return s;
          return { ...s, messages: [...s.messages, msg] };
        });
      } else if (data.type === "status") {
        setState((s) => ({ ...s, status: data.status as ConvStatus }));
      }
    };
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [customerId, enabled]);

  const send = useCallback((content: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "message", content }));
    }
  }, []);

  return { ...state, send };
}

// -------------------------------------------------------------- rating modal
function RatingModal({ customerId, conversationId, onClose }: {
  customerId: string;
  conversationId: string;
  onClose: () => void;
}) {
  const [stars, setStars] = useState(0);
  const [hover, setHover] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [tagOptions, setTagOptions] = useState<string[]>([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    fetchFeedbackTags().then(setTagOptions).catch(() => setTagOptions([]));
  }, []);

  const toggleTag = (t: string) =>
    setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));

  const submit = async () => {
    if (stars === 0) return;
    await sendRating({ customerId, conversationId, stars, tags, comment });
    setDone(true);
    setTimeout(onClose, 1200);
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal rating-modal" onClick={(e) => e.stopPropagation()}>
        {done ? (
          <p className="rating-thanks">🎉 感谢您的评价！</p>
        ) : (
          <>
            <h3>请为本次咨询体验打分</h3>
            <div className="stars">
              {[1, 2, 3, 4, 5].map((n) => (
                <span
                  key={n}
                  className={`star ${(hover || stars) >= n ? "on" : ""}`}
                  onMouseEnter={() => setHover(n)}
                  onMouseLeave={() => setHover(0)}
                  onClick={() => setStars(n)}
                >
                  ★
                </span>
              ))}
            </div>
            {stars > 0 && stars <= 3 && (
              <div className="rating-tags">
                <p className="muted">哪里需要改进？（可多选）</p>
                <div className="tag-row">
                  {tagOptions.map((t) => (
                    <button
                      key={t}
                      className={tags.includes(t) ? "tag on" : "tag"}
                      onClick={() => toggleTag(t)}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <textarea
              className="rating-comment"
              placeholder="补充说明（选填）"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            <button className="primary wide" disabled={stars === 0} onClick={submit}>
              提交评价
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- chat widget
function ChatWidget({ customerId, prefill, onConsumePrefill }: {
  customerId: string;
  prefill: string;
  onConsumePrefill: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const { messages, status, connected, conversationId, send } = useCustomerChat(customerId, open);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [rated, setRated] = useState<Record<number, "up" | "down">>({});
  const [showRating, setShowRating] = useState(false);
  const ratedConvRef = useRef<string>("");

  useEffect(() => {
    if (prefill && !open) setOpen(true);
    if (prefill) {
      setDraft(prefill);
      onConsumePrefill();
    }
  }, [prefill, open, onConsumePrefill]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [messages, open]);

  // When the conversation is resolved, prompt for an overall rating once.
  useEffect(() => {
    if (status === "resolved" && conversationId && ratedConvRef.current !== conversationId) {
      ratedConvRef.current = conversationId;
      setShowRating(true);
    }
  }, [status, conversationId]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    send(text);
    setDraft("");
  };

  const rate = (messageId: number, value: "up" | "down") => {
    if (!conversationId) return;
    setRated((r) => ({ ...r, [messageId]: value }));
    sendMessageFeedback(conversationId, messageId, customerId, value).catch(() => undefined);
  };

  const endChat = async () => {
    try {
      await endConversation(customerId);
    } catch {
      // even if the call fails, offer the rating dialog
      if (conversationId) setShowRating(true);
    }
  };

  const statusMeta =
    status === "queued"
      ? { title: "已转人工", desc: "问题已进入人工队列，坐席接入后会继续回复" }
      : status === "human"
      ? { title: "坐席已接入", desc: "当前由人工客服处理，AI 可辅助生成回复" }
      : status === "resolved"
      ? { title: "会话已结束", desc: "您可以重新发起咨询或提交本次评价" }
      : connected
      ? { title: "AI处理中", desc: "常规商品、订单、物流问题会优先由 AI 自动回复" }
      : { title: "连接中", desc: "正在建立客服会话" };

  return (
    <div className="chat-widget">
      {open && (
        <div className="chat-panel">
          <div className="chat-panel-head">
            <span>云市集客服</span>
            <div className="chat-head-actions">
              <button className="end-btn" onClick={endChat} title="结束咨询并评价">
                结束咨询
              </button>
              <button className="icon-btn" onClick={() => setOpen(false)}>
                ×
              </button>
            </div>
          </div>
          <div className={`chat-status ${status}`}>
            <strong>{statusMeta.title}</strong>
            <span>{statusMeta.desc}</span>
          </div>
          <div className="chat-body" ref={bodyRef}>
            {messages.length === 0 && (
              <p className="muted">您好，我是云市集智能客服，可以帮您查商品、订单、物流与优惠～</p>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`cmsg ${m.role}`}>
                {m.role === "system" ? (
                  <div className="cmsg-system">{m.content}</div>
                ) : (
                  <div className="cmsg-wrap">
                    <div className="cmsg-bubble">
                      {m.role === "agent" && <div className="cmsg-sender">{m.sender}</div>}
                      {m.content}
                    </div>
                    {m.role === "ai" && (
                      <div className="msg-fb">
                        <button
                          className={rated[m.id] === "up" ? "fb on" : "fb"}
                          onClick={() => rate(m.id, "up")}
                        >
                          👍
                        </button>
                        <button
                          className={rated[m.id] === "down" ? "fb on" : "fb"}
                          onClick={() => rate(m.id, "down")}
                        >
                          👎
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="chat-input">
            <input
              value={draft}
              placeholder="输入您的问题…"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
            />
            <button className="primary" onClick={submit}>
              发送
            </button>
          </div>
          <div className="chat-quick">
            <button onClick={() => send("我要转人工")}>转人工</button>
            <button onClick={() => send("我的订单状态")}>我的订单</button>
            <button onClick={() => send("怎么申请退货")}>退货</button>
            <button onClick={() => send("有什么优惠券")}>优惠券</button>
          </div>
        </div>
      )}
      <button className="chat-fab" onClick={() => setOpen((o) => !o)}>
        {open ? "收起" : "💬 客服"}
      </button>
      {showRating && conversationId && (
        <RatingModal
          customerId={customerId}
          conversationId={conversationId}
          onClose={() => setShowRating(false)}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------- storefront
interface StorefrontProps {
  initialCustomerId?: string;
  lockedCustomerId?: boolean;
  customerName?: string;
}

export default function Storefront({
  initialCustomerId = "c9001",
  lockedCustomerId = false,
  customerName = "",
}: StorefrontProps) {
  const [customerId, setCustomerId] = useState(initialCustomerId);
  const [categories, setCategories] = useState<string[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [stockFilter, setStockFilter] = useState<"all" | "in" | "out">("all");
  const [sortMode, setSortMode] = useState<"default" | "priceAsc" | "priceDesc" | "ratingDesc" | "salesDesc">("default");
  const [products, setProducts] = useState<Product[]>([]);
  const [detail, setDetail] = useState<Product | null>(null);
  const [prefill, setPrefill] = useState("");
  const [loading, setLoading] = useState(false);
  const [ordersOpen, setOrdersOpen] = useState(false);
  const [orders, setOrders] = useState<OrderLite[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [orderNotice, setOrderNotice] = useState("");
  const [activeShipment, setActiveShipment] = useState<Shipment | null>(null);

  useEffect(() => {
    fetchCategories().then(setCategories).catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    setCustomerId(initialCustomerId);
  }, [initialCustomerId]);

  const load = useCallback(() => {
    setLoading(true);
    fetchProducts({ query, limit: 100 })
      .then(setProducts)
      .catch(() => setProducts([]))
      .finally(() => setLoading(false));
  }, [query]);

  const loadOrders = useCallback(() => {
    if (!customerId.trim()) return;
    setOrdersLoading(true);
    setOrderNotice("");
    fetchCustomerOrders(customerId.trim(), 10)
      .then(setOrders)
      .catch(() => {
        setOrders([]);
        setOrderNotice("订单加载失败，请稍后重试。");
      })
      .finally(() => setOrdersLoading(false));
  }, [customerId]);

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ordersOpen) loadOrders();
  }, [ordersOpen, loadOrders]);

  const filteredProducts = useMemo(() => {
    const min = Number(minPrice);
    const max = Number(maxPrice);
    const hasMin = minPrice.trim() !== "" && Number.isFinite(min);
    const hasMax = maxPrice.trim() !== "" && Number.isFinite(max);
    const selected = new Set(selectedCategories);
    const filtered = products.filter((p) => {
      if (selected.size > 0 && !selected.has(p.category)) return false;
      if (hasMin && p.price < min) return false;
      if (hasMax && p.price > max) return false;
      if (stockFilter === "in" && !p.in_stock) return false;
      if (stockFilter === "out" && p.in_stock) return false;
      return true;
    });
    return [...filtered].sort((a, b) => {
      if (sortMode === "priceAsc") return a.price - b.price;
      if (sortMode === "priceDesc") return b.price - a.price;
      if (sortMode === "ratingDesc") return b.rating - a.rating;
      if (sortMode === "salesDesc") return b.rating_count - a.rating_count;
      return 0;
    });
  }, [products, selectedCategories, minPrice, maxPrice, stockFilter, sortMode]);

  const toggleCategory = (next: string) => {
    setSelectedCategories((cur) =>
      cur.includes(next) ? cur.filter((item) => item !== next) : [...cur, next]
    );
  };

  const openDetail = (productId: string) => {
    fetchProductDetail(productId).then(setDetail).catch(() => setDetail(null));
  };

  const askAbout = (p: Product) => {
    setPrefill(`我想咨询「${p.title}」(${p.product_id})`);
    setDetail(null);
  };

  const openOrders = () => {
    setOrdersOpen(true);
    setActiveShipment(null);
  };

  const consultOrder = (order: OrderLite) => {
    const items = (order.items ?? []).map((item) => item.title).join("、") || "订单商品";
    setPrefill(`我想咨询订单 ${order.order_id}，商品：${items}。`);
    setOrdersOpen(false);
  };

  const showShipment = async (order: OrderLite) => {
    setOrderNotice("");
    try {
      const shipment = order.shipment ?? (await fetchOrderShipment(order.order_id, customerId));
      setActiveShipment(shipment);
    } catch {
      setActiveShipment(null);
      setOrderNotice("该订单暂无物流信息。");
    }
  };

  const requestReturn = async (order: OrderLite) => {
    const firstItem = order.items?.[0];
    setOrderNotice("");
    try {
      const result = await createReturnRequest({
        customerId,
        orderId: order.order_id,
        skuCode: firstItem?.sku_code,
        reason: `用户从商城端申请退货：${firstItem?.title ?? order.order_id}`,
      });
      setOrderNotice(`售后申请已提交：${result.return_id}，预计退款 ${yuan(result.refund_amount)}。`);
    } catch {
      setOrderNotice("售后申请提交失败，请确认订单状态或联系人工客服。");
    }
  };

  return (
    <div className="store">
      <header className="store-head">
        <div className="store-brand">🛒 云市集 ServiceBot</div>
        <div className="store-search">
          <input
            value={query}
            placeholder="搜索商品，如 耳机 / 卫衣 / 台灯"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
          />
          <button className="primary" onClick={load}>
            搜索
          </button>
        </div>
        <button className="store-order-btn" onClick={openOrders}>
          我的订单
        </button>
        <label className={`store-cust ${lockedCustomerId ? "locked" : ""}`}>
          <span>{customerName ? `顾客：${customerName}` : "顾客ID"}</span>
          <input
            value={customerId}
            disabled={lockedCustomerId}
            title={lockedCustomerId ? "当前登录账号绑定的顾客ID" : undefined}
            onChange={(e) => setCustomerId(e.target.value)}
          />
        </label>
      </header>

      <div className="store-cats">
        <button
          className={selectedCategories.length === 0 ? "chip active" : "chip"}
          onClick={() => setSelectedCategories([])}
        >
          全部
        </button>
        {categories.map((c) => (
          <button
            key={c}
            className={selectedCategories.includes(c) ? "chip active" : "chip"}
            onClick={() => toggleCategory(c)}
          >
            {emojiFor(c)} {c}
          </button>
        ))}
      </div>

      <div className="store-filters">
        <label>
          最低价
          <input value={minPrice} inputMode="numeric" placeholder="0" onChange={(e) => setMinPrice(e.target.value)} />
        </label>
        <label>
          最高价
          <input value={maxPrice} inputMode="numeric" placeholder="不限" onChange={(e) => setMaxPrice(e.target.value)} />
        </label>
        <label>
          库存
          <select value={stockFilter} onChange={(e) => setStockFilter(e.target.value as typeof stockFilter)}>
            <option value="all">全部库存</option>
            <option value="in">仅看有货</option>
            <option value="out">仅看缺货</option>
          </select>
        </label>
        <label>
          排序
          <select value={sortMode} onChange={(e) => setSortMode(e.target.value as typeof sortMode)}>
            <option value="default">综合推荐</option>
            <option value="priceAsc">价格从低到高</option>
            <option value="priceDesc">价格从高到低</option>
            <option value="ratingDesc">评分优先</option>
            <option value="salesDesc">销量优先</option>
          </select>
        </label>
        <button onClick={load}>刷新商品</button>
      </div>

      {loading ? (
        <p className="muted store-pad">加载中…</p>
      ) : (
        <div className="product-grid">
          {filteredProducts.map((p) => (
            <div key={p.product_id} className="product-card" onClick={() => openDetail(p.product_id)}>
              <ProductVisual product={p} />
              <div className="product-title">{p.title}</div>
              <div className="product-brand">{p.brand} · {p.category}</div>
              <div className="product-row">
                <span className="price">{yuan(p.price)}</span>
                <span className="rating">★ {p.rating.toFixed(1)}</span>
              </div>
              <div className="product-sales">{p.rating_count.toLocaleString("zh-CN")} 人评价</div>
              <span className={p.in_stock ? "stock ok" : "stock out"}>
                {p.in_stock ? "有货" : "缺货"}
              </span>
            </div>
          ))}
          {filteredProducts.length === 0 && <p className="muted store-pad">没有找到相关商品。</p>}
        </div>
      )}

      {detail && (
        <div className="modal-mask" onClick={() => setDetail(null)}>
          <div className="modal detail-modal" onClick={(e) => e.stopPropagation()}>
            <button className="icon-btn modal-close" onClick={() => setDetail(null)}>
              ×
            </button>
            <div className="detail-layout">
              <div className="detail-gallery">
                <ProductVisual product={detail} large />
                <div className="detail-thumbs">
                  <div className="detail-thumb">
                    <ProductVisual product={detail} />
                  </div>
                  <div className="detail-thumb detail-material">材质细节</div>
                  <div className="detail-thumb detail-scene">场景图</div>
                </div>
              </div>
              <div className="detail-summary">
                <h2>{detail.title}</h2>
                <p className="muted">{detail.brand} · {detail.category}</p>
                <p className="price big">{yuan(detail.price)}</p>
                <p>★ {detail.rating.toFixed(1)} · {detail.rating_count} 条评价</p>
              </div>
            </div>
            <p>{detail.description}</p>
            <h4>商品参数</h4>
            <div className="param-grid">
              {Object.entries(detail.attributes ?? {}).map(([key, value]) => (
                <div key={key}>
                  <span>{key}</span>
                  <strong>{String(value)}</strong>
                </div>
              ))}
              <div>
                <span>商品编号</span>
                <strong>{detail.product_id}</strong>
              </div>
              <div>
                <span>库存状态</span>
                <strong>{detail.in_stock ? "现货可售" : "暂时缺货"}</strong>
              </div>
            </div>
            <h4>规格</h4>
            <div className="variant-list">
              {(detail.variants ?? []).map((v) => (
                <div key={v.sku_code} className="variant">
                  <span>
                    {Object.values(v.attributes).join(" / ")}
                  </span>
                  <span className="price">{yuan(v.price)}</span>
                  <span className={v.in_stock ? "stock ok" : "stock out"}>
                    {v.in_stock ? `库存 ${v.stock}` : "缺货"}
                  </span>
                </div>
              ))}
            </div>
            <h4>售后说明</h4>
            <div className="after-sale-box">
              <span>7天无理由退货</span>
              <span>质量问题优先转人工</span>
              <span>物流异常可一键咨询订单</span>
            </div>
            <button className="primary wide" onClick={() => askAbout(detail)}>
              咨询客服
            </button>
            <ProductReviews productId={detail.product_id} customerId={customerId} />
          </div>
        </div>
      )}

      {ordersOpen && (
        <div className="modal-mask" onClick={() => setOrdersOpen(false)}>
          <div className="modal order-modal" onClick={(e) => e.stopPropagation()}>
            <button className="icon-btn modal-close" onClick={() => setOrdersOpen(false)}>
              ×
            </button>
            <div className="order-head">
              <div>
                <h2>我的订单</h2>
                <p className="muted">可查看物流、申请退货，或携带订单信息咨询客服。</p>
              </div>
              <button onClick={loadOrders}>刷新</button>
            </div>
            {orderNotice && <div className="order-notice">{orderNotice}</div>}
            {ordersLoading ? (
              <p className="muted store-pad">订单加载中…</p>
            ) : (
              <div className="order-list">
                {orders.map((order) => (
                  <div className="order-card" key={order.order_id}>
                    <div className="order-card-top">
                      <strong>{order.order_id}</strong>
                      <span className={`order-status ${order.status}`}>{orderStatusText(order.status)}</span>
                    </div>
                    <div className="order-meta">
                      <span>{timeText(order.created_at)}</span>
                      <span>{order.shipping_method ?? "暂无配送方式"}</span>
                      <span className="price">{yuan(order.total)}</span>
                    </div>
                    <div className="order-items">
                      {(order.items ?? []).map((item) => (
                        <div key={`${order.order_id}-${item.sku_code ?? item.title}`}>
                          <span>{item.title}</span>
                          <span>x{item.qty}</span>
                        </div>
                      ))}
                    </div>
                    <div className="order-actions">
                      <button onClick={() => showShipment(order)}>查看物流</button>
                      <button onClick={() => requestReturn(order)}>申请退货</button>
                      <button className="primary" onClick={() => consultOrder(order)}>咨询此订单</button>
                    </div>
                  </div>
                ))}
                {orders.length === 0 && <p className="muted store-pad">当前账号暂无订单。</p>}
              </div>
            )}
            {activeShipment && (
              <div className="shipment-panel">
                <div className="ctx-title">
                  物流：{activeShipment.carrier} · {activeShipment.tracking_no}
                </div>
                {(activeShipment.events ?? []).map((event) => (
                  <div className="shipment-event" key={`${event.time}-${event.desc}`}>
                    <span>{timeText(event.time)}</span>
                    <strong>{event.desc}</strong>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <ChatWidget
        customerId={customerId}
        prefill={prefill}
        onConsumePrefill={() => setPrefill("")}
      />
    </div>
  );
}
