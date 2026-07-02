import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type {
  Conversation,
  StoreAnalytics,
  ProductAnalyticsRow,
  ProductAnalyticsDetail,
  PotentialTier,
} from "./shopTypes";
import {
  fetchAdminConversations,
  fetchAdminMe,
  fetchAdminOverview,
  fetchAdminProducts,
  fetchAdminRatings,
  fetchAdminUsers,
  fetchStoreAnalytics,
  fetchProductAnalytics,
  fetchProductAnalyticsDetail,
  requestProductAiInsight,
  generateProductReviews,
  loginAdmin,
  logoutAdmin,
} from "./shopApi";
import type { AdminOverview, AdminProduct, AdminRating, AdminUser } from "./shopApi";

const TOKEN_KEY = "servicebot_admin_token";
type AdminTab = "overview" | "analytics" | "conversations" | "products" | "feedback" | "users";

const STATUS_LABEL: Record<string, string> = {
  bot: "AI 接待",
  queued: "待人工",
  human: "人工中",
  resolved: "已完结",
};

const ORDER_STATUS_LABEL: Record<string, string> = {
  pending_payment: "待付款",
  shipped: "已发货",
  delivered: "已签收",
};

function money(n: number): string {
  return `¥${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function formatTime(ts?: number | null): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function stars(n: number): string {
  return "★★★★★".slice(0, n) + "☆☆☆☆☆".slice(0, Math.max(0, 5 - n));
}

const TIER_KEYS = ["star", "rising", "stable", "at_risk", "unrated"] as const;
const TIER_META: Record<string, { label: string; color: string }> = {
  star: { label: "明星", color: "#16a34a" },
  rising: { label: "潜力", color: "#0ea5e9" },
  stable: { label: "平稳", color: "#a16207" },
  at_risk: { label: "预警", color: "#dc2626" },
  unrated: { label: "待评价", color: "#64748b" },
};

function TierBadge({ tier }: { tier: PotentialTier }) {
  const meta = TIER_META[tier.key] ?? TIER_META.unrated;
  return (
    <span className={`tier-badge tier-${tier.key}`} style={{ borderColor: meta.color, color: meta.color }}>
      {meta.label}
    </span>
  );
}

function scoreColor(score: number): string {
  if (score >= 72) return "#16a34a";
  if (score >= 58) return "#0ea5e9";
  if (score >= 42) return "#a16207";
  return "#dc2626";
}

function trendArrow(t: number): ReactNode {
  if (t > 0.1) return <span className="trend up">↑ {t.toFixed(2)}</span>;
  if (t < -0.1) return <span className="trend down">↓ {Math.abs(t).toFixed(2)}</span>;
  return <span className="trend flat">→ 0</span>;
}

function Metric({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div className="admin-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return <span className={`admin-status status-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export default function AdminDashboard() {
  const [token, setToken] = useState(() =>
    typeof window === "undefined" ? "" : window.localStorage.getItem(TOKEN_KEY) ?? ""
  );
  const [user, setUser] = useState<AdminUser | null>(null);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin123456");
  const [activeTab, setActiveTab] = useState<AdminTab>("overview");
  const [statusFilter, setStatusFilter] = useState("all");
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [products, setProducts] = useState<AdminProduct[]>([]);
  const [ratings, setRatings] = useState<AdminRating[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadDashboard = useCallback(
    async (authToken: string, filter = statusFilter) => {
      setLoading(true);
      setError("");
      try {
        const [me, nextOverview, nextConversations, nextProducts, nextRatings, nextUsers] = await Promise.all([
          fetchAdminMe(authToken),
          fetchAdminOverview(authToken),
          fetchAdminConversations(authToken, filter),
          fetchAdminProducts(authToken),
          fetchAdminRatings(authToken),
          fetchAdminUsers(authToken),
        ]);
        setUser(me.user);
        setOverview(nextOverview);
        setConversations(nextConversations);
        setProducts(nextProducts);
        setRatings(nextRatings);
        setUsers(nextUsers);
      } catch (err) {
        const message = err instanceof Error ? err.message : "后台数据加载失败";
        setError(message);
        if (message.includes("401")) {
          window.localStorage.removeItem(TOKEN_KEY);
          setToken("");
        }
      } finally {
        setLoading(false);
      }
    },
    [statusFilter]
  );

  useEffect(() => {
    if (token) void loadDashboard(token);
  }, [token, loadDashboard]);

  useEffect(() => {
    if (!token) return;
    fetchAdminConversations(token, statusFilter).then(setConversations).catch(() => undefined);
  }, [statusFilter, token]);

  const satisfaction = useMemo(() => {
    const value = overview?.feedback.messages.satisfaction;
    return value == null ? "-" : `${Math.round(value * 100)}%`;
  }, [overview]);

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const session = await loginAdmin(username.trim(), password);
      window.localStorage.setItem(TOKEN_KEY, session.access_token);
      setToken(session.access_token);
      setUser(session.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = async () => {
    if (token) {
      await logoutAdmin(token).catch(() => undefined);
    }
    window.localStorage.removeItem(TOKEN_KEY);
    setToken("");
    setUser(null);
    setOverview(null);
  };

  if (!token) {
    return (
      <main className="admin-shell admin-login-shell">
        <form className="admin-login" onSubmit={handleLogin}>
          <div>
            <p className="admin-kicker">Backoffice</p>
            <h1>电商售后客服后台</h1>
          </div>
          <label>
            账号
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          </label>
          <label>
            密码
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
          {error && <div className="admin-alert">{error}</div>}
          <button className="primary wide" disabled={loading}>
            {loading ? "登录中..." : "登录"}
          </button>
          <p className="admin-demo-account">演示账号：admin / admin123456</p>
        </form>
      </main>
    );
  }

  return (
    <main className="admin-shell">
      <header className="admin-topbar">
        <div>
          <p className="admin-kicker">Backoffice</p>
          <h1>电商售后客服与评价分析后台</h1>
        </div>
        <div className="admin-account">
          <span>{user?.display_name ?? user?.username ?? "管理员"}</span>
          <button onClick={() => loadDashboard(token)} disabled={loading}>
            刷新
          </button>
          <button onClick={handleLogout}>退出</button>
        </div>
      </header>

      {error && <div className="admin-alert">{error}</div>}

      <nav className="admin-tabs">
        {[
          ["overview", "总览"],
          ["analytics", "潜力分析"],
          ["conversations", "会话"],
          ["products", "商品"],
          ["feedback", "服务评价"],
          ["users", "账号"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={activeTab === key ? "active" : ""}
            onClick={() => setActiveTab(key as AdminTab)}
          >
            {label}
          </button>
        ))}
      </nav>

      {activeTab === "overview" && (
        <section className="admin-view">
          <div className="admin-metrics">
            <Metric label="商品" value={overview?.catalog.active_products ?? "-"} note={`SKU ${overview?.catalog.skus ?? 0}`} />
            <Metric label="订单" value={overview?.catalog.orders ?? "-"} note={money(overview?.catalog.revenue ?? 0)} />
            <Metric
              label="会话"
              value={overview?.conversations.total ?? "-"}
              note={`待人工 ${overview?.conversations.by_status.queued ?? 0}`}
            />
            <Metric label="平均评分" value={overview?.feedback.ratings.avg_stars || "-"} note={`${overview?.feedback.ratings.count ?? 0} 条`} />
            <Metric label="消息满意度" value={satisfaction} note={`${overview?.feedback.messages.total ?? 0} 次反馈`} />
            <Metric label="在线坐席" value={overview?.agents.online_agents ?? 0} note={`账号 ${users.length}`} />
          </div>

          <div className="admin-layout two">
            <section className="admin-panel">
              <div className="admin-section-head">
                <h2>会话状态</h2>
                <span>{formatTime(overview?.generated_at)}</span>
              </div>
              <div className="admin-status-grid">
                {["bot", "queued", "human", "resolved"].map((status) => (
                  <div key={status}>
                    <StatusPill status={status} />
                    <strong>{overview?.conversations.by_status[status] ?? 0}</strong>
                  </div>
                ))}
              </div>
            </section>

            <section className="admin-panel">
              <div className="admin-section-head">
                <h2>库存与订单</h2>
                <span>低库存 SKU {overview?.catalog.low_stock_skus ?? 0}</span>
              </div>
              <div className="admin-bars">
                {Object.entries(overview?.catalog.orders_by_status ?? {}).map(([status, count]) => (
                  <div key={status} className="admin-bar-row">
                    <span>{ORDER_STATUS_LABEL[status] ?? status}</span>
                    <div>
                      <i style={{ width: `${Math.max(8, count * 22)}px` }} />
                    </div>
                    <b>{count}</b>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="admin-layout two">
            <section className="admin-panel">
              <div className="admin-section-head">
                <h2>商品分类</h2>
                <span>库存 {overview?.catalog.total_stock ?? 0}</span>
              </div>
              <div className="admin-category-list">
                {(overview?.catalog.categories ?? []).map((item) => (
                  <div key={item.category}>
                    <span>{item.category}</span>
                    <strong>{item.count}</strong>
                  </div>
                ))}
              </div>
            </section>

            <section className="admin-panel">
              <div className="admin-section-head">
                <h2>最近会话</h2>
                <button onClick={() => setActiveTab("conversations")}>查看</button>
              </div>
              <div className="admin-mini-list">
                {(overview?.conversations.latest ?? []).map((item) => (
                  <div key={item.conversation_id}>
                    <span>{item.customer_id}</span>
                    <StatusPill status={item.status} />
                    <small>{formatTime(item.last_message_at)}</small>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </section>
      )}

      {activeTab === "analytics" && <AnalyticsView token={token} />}

      {activeTab === "conversations" && (
        <section className="admin-view">
          <div className="admin-section-head">
            <h2>会话管理</h2>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">全部</option>
              <option value="bot">AI 接待</option>
              <option value="queued">待人工</option>
              <option value="human">人工中</option>
              <option value="resolved">已完结</option>
            </select>
          </div>
          <AdminTable
            columns={["会话ID", "客户", "状态", "优先级", "坐席", "CSAT", "最后消息"]}
            rows={conversations.map((item) => [
              item.conversation_id,
              item.customer_id,
              <StatusPill status={item.status} />,
              item.priority,
              item.assigned_agent || "-",
              item.csat ?? "-",
              formatTime(item.last_message_at),
            ])}
          />
        </section>
      )}

      {activeTab === "products" && (
        <section className="admin-view">
          <div className="admin-section-head">
            <h2>商品与库存</h2>
            <span>{products.length} 个商品</span>
          </div>
          <AdminTable
            columns={["商品ID", "名称", "分类", "品牌", "价格", "SKU", "库存", "状态"]}
            rows={products.map((item) => [
              item.product_id,
              item.title,
              item.category,
              item.brand,
              money(item.price),
              item.sku_count,
              item.stock_total,
              item.status,
            ])}
          />
        </section>
      )}

      {activeTab === "feedback" && (
        <section className="admin-view">
          <div className="admin-section-head">
            <h2>用户评价分析</h2>
            <span>平均 {overview?.feedback.ratings.avg_stars ?? 0} 分</span>
          </div>
          <div className="admin-feedback-band">
            {[5, 4, 3, 2, 1].map((score) => (
              <div key={score}>
                <span>{score} 星</span>
                <strong>{overview?.feedback.ratings.distribution[String(score)] ?? 0}</strong>
              </div>
            ))}
            {(overview?.feedback.top_tags ?? []).map((tag) => (
              <div key={tag.tag}>
                <span>{tag.tag}</span>
                <strong>{tag.count}</strong>
              </div>
            ))}
          </div>
          <AdminTable
            columns={["会话ID", "客户", "评分", "标签", "评论", "时间"]}
            rows={ratings.map((item) => [
              item.conversation_id,
              item.customer_id,
              stars(item.stars),
              item.tags.join("、") || "-",
              item.comment || "-",
              formatTime(item.created_at),
            ])}
          />
        </section>
      )}

      {activeTab === "users" && (
        <section className="admin-view">
          <div className="admin-section-head">
            <h2>管理员账号</h2>
            <span>{users.length} 个账号</span>
          </div>
          <AdminTable
            columns={["账号", "显示名", "角色", "创建时间", "最近登录"]}
            rows={users.map((item) => [
              item.username,
              item.display_name,
              item.role,
              formatTime(item.created_at),
              formatTime(item.last_login_at),
            ])}
          />
        </section>
      )}
    </main>
  );
}

function ScoreRing({ score, size = 120 }: { score: number; size?: number }) {
  const color = scoreColor(score);
  const deg = Math.round((score / 100) * 360);
  return (
    <div
      className="score-ring"
      style={{ width: size, height: size, background: `conic-gradient(${color} ${deg}deg, #e5e7eb 0deg)` }}
    >
      <div className="score-ring-hole">
        <strong style={{ color }}>{score.toFixed(0)}</strong>
        <small>潜力分</small>
      </div>
    </div>
  );
}

function InsightColumn({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  if (!items || items.length === 0) return null;
  return (
    <div className={`insight-col ${tone}`}>
      <h5>{title}</h5>
      <ul>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

const SORT_OPTIONS: Array<[string, string]> = [
  ["score", "潜力分"],
  ["rating", "评分"],
  ["reviews", "评价数"],
  ["trend", "增长势能"],
];

function AnalyticsView({ token }: { token: string }) {
  const [store, setStore] = useState<StoreAnalytics | null>(null);
  const [rows, setRows] = useState<ProductAnalyticsRow[]>([]);
  const [sort, setSort] = useState("score");
  const [loading, setLoading] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const loadStore = useCallback(
    async (ai: boolean) => {
      const data = await fetchStoreAnalytics(token, ai);
      setStore(data);
    },
    [token]
  );

  useEffect(() => {
    setLoading(true);
    Promise.all([loadStore(false), fetchProductAnalytics(token, { sort })])
      .then(([, products]) => setRows(products))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [token, sort, loadStore]);

  const runAiSummary = async () => {
    setAiBusy(true);
    try {
      await loadStore(true);
    } finally {
      setAiBusy(false);
    }
  };

  const sp = store?.store;
  const summary = store?.summary;
  const maxCatScore = Math.max(1, ...(sp?.categories ?? []).map((c) => c.avg_score));

  return (
    <section className="admin-view">
      {sp && (
        <div className="an-hero">
          <div className="an-hero-score">
            <ScoreRing score={sp.score} />
            <div className="an-hero-tier">
              <TierBadge tier={sp.tier} />
              <p>{sp.tier.advice}</p>
            </div>
          </div>
          <div className="an-hero-kpis">
            <Metric label="平均评分" value={sp.avg_rating.toFixed(2)} note={`${sp.total_reviews} 条评价`} />
            <Metric label="好评率" value={`${Math.round(sp.positive_share * 100)}%`} note={`差评 ${Math.round(sp.negative_share * 100)}%`} />
            <Metric label="整体趋势" value={sp.rating_trend > 0 ? `+${sp.rating_trend.toFixed(2)}` : sp.rating_trend.toFixed(2)} note="近30天 vs 更早" />
            <Metric label="已评商品" value={`${sp.rated_products}/${sp.total_products}`} note="覆盖率" />
          </div>
          <div className="an-hero-tiers">
            {TIER_KEYS.map((k) => (
              <div key={k} className={`an-tier-chip tier-${k}`}>
                <span>{TIER_META[k].label}</span>
                <strong>{sp.tier_counts[k] ?? 0}</strong>
              </div>
            ))}
          </div>
        </div>
      )}

      {summary && (
        <section className="admin-panel an-summary">
          <div className="admin-section-head">
            <h2>
              AI 经营洞察
              <span className={`gen-badge ${summary.generated_by}`}>
                {summary.generated_by === "ai" ? "AI 生成" : "规则引擎"}
              </span>
            </h2>
            <button onClick={runAiSummary} disabled={aiBusy}>
              {aiBusy ? "分析中…" : "🤖 生成 AI 洞察"}
            </button>
          </div>
          <p className="an-headline">{summary.headline}</p>
          <div className="insight-cols">
            <InsightColumn title="✅ 亮点" items={summary.highlights} tone="good" />
            <InsightColumn title="⚠️ 风险" items={summary.concerns} tone="bad" />
            <InsightColumn title="💡 机会" items={summary.opportunities} tone="info" />
            <InsightColumn title="🎯 行动建议" items={summary.strategic_actions} tone="action" />
          </div>
        </section>
      )}

      {sp && sp.categories.length > 0 && (
        <section className="admin-panel">
          <div className="admin-section-head">
            <h2>分类潜力</h2>
            <span>按潜力分排序</span>
          </div>
          <div className="an-cat-list">
            {sp.categories.map((c) => (
              <div key={c.category} className="an-cat-row">
                <span className="an-cat-name">{c.category}</span>
                <div className="an-cat-bar">
                  <i style={{ width: `${(c.avg_score / maxCatScore) * 100}%`, background: scoreColor(c.avg_score) }} />
                </div>
                <b style={{ color: scoreColor(c.avg_score) }}>{c.avg_score}</b>
                <small>{c.products} 款 · {c.reviews} 评价 · ★{c.avg_rating}</small>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="admin-panel">
        <div className="admin-section-head">
          <h2>商品发展潜力榜</h2>
          <div className="an-sort">
            <span>排序</span>
            <select value={sort} onChange={(e) => setSort(e.target.value)}>
              {SORT_OPTIONS.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
        {loading && <p className="muted">加载中…</p>}
        <div className="an-rank">
          {rows.map((r, idx) => (
            <button key={r.product_id} className="an-rank-row" onClick={() => setSelected(r.product_id)}>
              <span className="an-rank-idx">{idx + 1}</span>
              <span className="an-rank-title">
                {r.title}
                <small>{r.category} · {r.review_count} 评价 · ★{r.avg_rating}</small>
              </span>
              <span className="an-rank-bar">
                <i style={{ width: `${r.score}%`, background: scoreColor(r.score) }} />
              </span>
              <b className="an-rank-score" style={{ color: scoreColor(r.score) }}>
                {r.score.toFixed(0)}
              </b>
              {trendArrow(r.rating_trend)}
              <TierBadge tier={r.tier} />
            </button>
          ))}
          {!loading && rows.length === 0 && <p className="admin-empty">暂无商品数据</p>}
        </div>
      </section>

      {selected && (
        <ProductDrawer token={token} productId={selected} onClose={() => setSelected(null)} onChanged={() => {
          void fetchProductAnalytics(token, { sort }).then(setRows).catch(() => undefined);
          void loadStore(false).catch(() => undefined);
        }} />
      )}
    </section>
  );
}

function ProductDrawer({
  token,
  productId,
  onClose,
  onChanged,
}: {
  token: string;
  productId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<ProductAnalyticsDetail | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [genBusy, setGenBusy] = useState(false);

  const load = useCallback(
    (ai: boolean) => {
      fetchProductAnalyticsDetail(token, productId, ai).then(setDetail).catch(() => undefined);
    },
    [token, productId]
  );

  useEffect(() => {
    load(false);
  }, [load]);

  const runAi = async () => {
    setAiBusy(true);
    try {
      const res = await requestProductAiInsight(token, productId);
      setDetail((d) => (d ? { ...d, insight: res.insight, potential: res.potential } : d));
    } finally {
      setAiBusy(false);
    }
  };

  const genReviews = async () => {
    setGenBusy(true);
    try {
      await generateProductReviews(token, productId, 5, "mixed");
      load(false);
      onChanged();
    } finally {
      setGenBusy(false);
    }
  };

  const p = detail?.potential;
  const s = detail?.stats;
  const insight = detail?.insight;
  const total = s?.count ?? 0;
  const riskClass = insight ? `risk-${insight.risk_level}` : "";

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal an-drawer" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn modal-close" onClick={onClose}>
          ×
        </button>
        {!detail ? (
          <p className="muted">加载中…</p>
        ) : (
          <>
            <div className="an-drawer-head">
              <div>
                <h2>{detail.product.title}</h2>
                <p className="muted">
                  {detail.product.brand} · {detail.product.category} · ¥{detail.product.price}
                </p>
              </div>
              {p && (
                <div className="an-drawer-score">
                  <ScoreRing score={p.score} size={92} />
                  <TierBadge tier={p.tier} />
                </div>
              )}
            </div>

            <div className="an-drawer-actions">
              <button className="primary" onClick={runAi} disabled={aiBusy}>
                {aiBusy ? "分析中…" : "🤖 生成 AI 洞察"}
              </button>
              <button onClick={genReviews} disabled={genBusy}>
                {genBusy ? "生成中…" : "✍️ AI 生成评价"}
              </button>
            </div>

            {p && p.drivers && (
              <div className="an-drivers">
                {p.drivers.map((d) => (
                  <div key={d.key} className={`an-driver tone-${d.tone}`}>
                    <span>{d.label}</span>
                    <i>
                      <b style={{ width: `${Math.round(d.value * 100)}%` }} />
                    </i>
                    <small>{d.reason}</small>
                  </div>
                ))}
              </div>
            )}

            {insight && (
              <section className={`an-insight ${riskClass}`}>
                <div className="an-insight-head">
                  <h4>
                    AI 商品洞察
                    <span className={`gen-badge ${insight.generated_by}`}>
                      {insight.generated_by === "ai" ? "AI 生成" : "规则引擎"}
                    </span>
                  </h4>
                  <span className={`risk-pill ${riskClass}`}>
                    风险：{insight.risk_level === "high" ? "高" : insight.risk_level === "medium" ? "中" : "低"}
                  </span>
                </div>
                <p className="an-insight-summary">{insight.summary}</p>
                <div className="insight-cols">
                  <InsightColumn title="✅ 优点" items={insight.pros} tone="good" />
                  <InsightColumn title="⚠️ 缺点" items={insight.cons} tone="bad" />
                  <InsightColumn title="🎯 建议动作" items={insight.recommended_actions} tone="action" />
                </div>
                {insight.potential_narrative && (
                  <p className="an-narrative">{insight.potential_narrative}</p>
                )}
              </section>
            )}

            {s && (
              <div className="an-drawer-stats">
                <div className="an-dist">
                  {[5, 4, 3, 2, 1].map((star) => {
                    const c = s.distribution[String(star)] ?? 0;
                    const pct = total ? Math.round((c / total) * 100) : 0;
                    return (
                      <div key={star} className="pr-dist-row">
                        <span>{star}★</span>
                        <i>
                          <b style={{ width: `${pct}%` }} />
                        </i>
                        <em>{c}</em>
                      </div>
                    );
                  })}
                </div>
                <div className="an-side-stats">
                  <div><span>好评率</span><strong>{Math.round(s.positive_share * 100)}%</strong></div>
                  <div><span>差评率</span><strong>{Math.round(s.negative_share * 100)}%</strong></div>
                  <div><span>评分趋势</span><strong>{trendArrow(s.rating_trend)}</strong></div>
                  <div><span>销量</span><strong>{detail.demand.units} 件</strong></div>
                </div>
              </div>
            )}

            {s && s.top_tags.length > 0 && (
              <div className="an-tags">
                {s.top_tags.map((t) => (
                  <span key={t.tag} className="pr-tagchip">{t.tag} {t.count}</span>
                ))}
              </div>
            )}

            <div className="an-reviews">
              <h4>评价明细（{detail.reviews.length}）</h4>
              {detail.reviews.slice(0, 12).map((r) => (
                <div key={r.review_id} className="an-review-item">
                  <span className={`pr-item-stars s${r.rating}`}>{"★★★★★".slice(0, r.rating)}</span>
                  {r.source === "ai" && <span className="pr-badge ai">AI</span>}
                  <span className="an-review-text">{r.content}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function AdminTable({ columns, rows }: { columns: string[]; rows: Array<Array<ReactNode>> }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="admin-empty">
                暂无数据
              </td>
            </tr>
          ) : (
            rows.map((row, idx) => (
              <tr key={idx}>
                {row.map((cell, cellIdx) => (
                  <td key={cellIdx}>{cell}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
