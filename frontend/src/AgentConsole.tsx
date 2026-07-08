import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Conversation,
  ConvMessage,
  AgentContext,
  ConvStatus,
  BusEvent,
} from "./shopTypes";
import {
  fetchAgentConversations,
  fetchAgentContext,
  fetchAgentStats,
  claimConversation,
  sendAgentMessage,
  releaseConversation,
  resolveConversation,
  suggestReply,
  fetchFeedbackSummary,
  agentSocketUrl,
  conversationSocketUrl,
} from "./shopApi";
import type { FeedbackSummary } from "./shopApi";

const REASON_LABEL: Record<string, string> = {
  user_requested_human: "用户要求人工",
  complaint_or_legal: "投诉/法律",
  sensitive_account: "账户/资金安全",
  user_frustrated: "用户不满/重复",
  high_value_return: "大额退款",
  tool_failure: "系统故障",
  no_answer_found: "未查到结果",
};

const PRIO_CLASS: Record<number, string> = { 1: "low", 2: "medium", 3: "high", 4: "urgent" };
const CONSOLE_STATE_KEY = "servicebot_agent_console_state_v1";
const REFRESH_INTERVAL_MS = 8000;

interface SavedConsoleState {
  agentId?: string;
  agentName?: string;
  filter?: string;
  selectedId?: string;
}

function readSavedConsoleState(): SavedConsoleState {
  if (typeof window === "undefined") return {};
  const raw = window.localStorage.getItem(CONSOLE_STATE_KEY);
  if (!raw) return {};
  try {
    return JSON.parse(raw) as SavedConsoleState;
  } catch {
    window.localStorage.removeItem(CONSOLE_STATE_KEY);
    return {};
  }
}

function statusBadge(s: ConvStatus): string {
  return s === "queued" ? "排队中" : s === "human" ? "人工中" : s === "resolved" ? "已结束" : "AI中";
}

function timeago(ts: number): string {
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)}分钟前`;
  return `${Math.floor(d / 3600)}小时前`;
}

function actionErrorMessage(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (text.includes("conversation_assigned_to_other_agent")) return "该会话已被其他坐席接入，请刷新列表。";
  if (text.includes("conversation_not_claimable")) return "该会话状态已变化，暂时不能认领。";
  if (text.includes("conversation_not_found")) return "会话不存在或已结束，请重新选择。";
  if (text.includes("401") || text.includes("403")) return "登录状态已失效，请重新登录管理后台。";
  if (text.includes("422")) return "请求参数不完整，请刷新页面后重试。";
  return `操作失败，请稍后重试。${text ? `（${text.slice(0, 120)}）` : ""}`;
}

interface AgentConsoleProps {
  adminToken: string;
  initialAgentId?: string;
  initialAgentName?: string;
}

export default function AgentConsole({
  adminToken,
  initialAgentId = "agent-1",
  initialAgentName = "客服小美",
}: AgentConsoleProps) {
  const saved = useRef<SavedConsoleState>(readSavedConsoleState());
  const [agentId, setAgentId] = useState(saved.current.agentId || initialAgentId);
  const [agentName, setAgentName] = useState(saved.current.agentName || initialAgentName);
  const [filter, setFilter] = useState<string>(saved.current.filter || "queued");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState<string>(saved.current.selectedId || "");
  const [context, setContext] = useState<AgentContext | null>(null);
  const [draft, setDraft] = useState("");
  const [stats, setStats] = useState<{ counts: Record<string, number>; online_agents: number }>({
    counts: {},
    online_agents: 0,
  });
  const [fb, setFb] = useState<FeedbackSummary | null>(null);
  const [claimingId, setClaimingId] = useState("");
  const [actionError, setActionError] = useState("");
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const claimInFlightRef = useRef(false);
  const selectedRef = useRef<string>("");
  selectedRef.current = selectedId;

  useEffect(() => {
    if (!saved.current.agentId) setAgentId(initialAgentId);
    if (!saved.current.agentName) setAgentName(initialAgentName);
  }, [initialAgentId, initialAgentName]);

  useEffect(() => {
    window.localStorage.setItem(
      CONSOLE_STATE_KEY,
      JSON.stringify({ agentId, agentName, filter, selectedId })
    );
  }, [agentId, agentName, filter, selectedId]);

  const refreshList = useCallback((nextFilter = filter) => {
    fetchAgentConversations(adminToken, nextFilter).then(setConversations).catch(() => setConversations([]));
    fetchAgentStats(adminToken).then(setStats).catch(() => undefined);
    fetchFeedbackSummary().then(setFb).catch(() => undefined);
  }, [adminToken, filter]);

  const refreshContext = useCallback(
    (cid: string) => {
      if (!cid) return;
      fetchAgentContext(adminToken, cid).then(setContext).catch(() => {
        setContext(null);
        setSelectedId("");
      });
    },
    [adminToken]
  );

  useEffect(() => {
    refreshList();
    const timer = window.setInterval(refreshList, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refreshList]);

  useEffect(() => {
    if (!selectedId && conversations.length > 0) {
      setSelectedId(conversations[0].conversation_id);
    }
  }, [conversations, selectedId]);

  // Agent notification socket: presence + queue/escalation events -> refresh.
  useEffect(() => {
    if (!agentId) return;
    const ws = new WebSocket(agentSocketUrl(agentId, adminToken));
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data) as BusEvent;
      refreshList();
      if (
        (data.type === "customer_message" || data.type === "ai_message" || data.type === "escalation") &&
        data.conversation_id === selectedRef.current
      ) {
        refreshContext(selectedRef.current);
      }
    };
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 20000);
    return () => {
      clearInterval(ping);
      ws.close();
    };
  }, [agentId, adminToken, refreshList, refreshContext]);

  // Live feed of the open conversation.
  useEffect(() => {
    if (!selectedId) return;
    refreshContext(selectedId);
    const ws = new WebSocket(conversationSocketUrl(selectedId, adminToken));
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data) as BusEvent;
      if (data.type === "message") {
        const msg = data.message as ConvMessage;
        setContext((c) =>
          c && !c.messages.some((m) => m.id === msg.id)
            ? { ...c, messages: [...c.messages, msg] }
            : c
        );
      } else if (data.type === "status") {
        setContext((c) =>
          c ? { ...c, conversation: { ...c.conversation, status: data.status as ConvStatus } } : c
        );
        refreshList();
      }
    };
    return () => ws.close();
  }, [selectedId, adminToken, refreshContext, refreshList]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [context?.messages]);

  useEffect(() => {
    setActionError("");
  }, [selectedId]);

  const conv = context?.conversation;
  const doClaim = async () => {
    const targetId = selectedId;
    const trimmedAgentId = agentId.trim();
    const trimmedAgentName = agentName.trim() || trimmedAgentId;
    if (!targetId || claimInFlightRef.current) return;
    if (!trimmedAgentId) {
      setActionError("请先填写坐席ID。");
      return;
    }
    claimInFlightRef.current = true;
    setClaimingId(targetId);
    setActionError("");
    try {
      const claimed = await claimConversation(adminToken, targetId, trimmedAgentId, trimmedAgentName, {
        conversation: context?.conversation,
        messages: context?.messages,
      });
      setContext((current) =>
        current && current.conversation.conversation_id === targetId
          ? { ...current, conversation: claimed }
          : current
      );
      setConversations((items) =>
        items.map((item) => (item.conversation_id === targetId ? claimed : item))
      );
      setFilter("human");
      refreshContext(targetId);
      refreshList("human");
    } catch (error) {
      setActionError(actionErrorMessage(error));
      refreshContext(targetId);
      refreshList();
    } finally {
      claimInFlightRef.current = false;
      setClaimingId("");
    }
  };
  const doSend = async () => {
    const text = draft.trim();
    if (!text || !selectedId) return;
    await sendAgentMessage(adminToken, selectedId, agentId, text, {
      conversation: context?.conversation,
      messages: context?.messages,
    });
    setDraft("");
    refreshContext(selectedId);
  };
  const doSuggest = async () => {
    if (!selectedId) return;
    const s = await suggestReply(adminToken, selectedId);
    if (s) setDraft(s);
  };
  const doRelease = async () => {
    if (!selectedId) return;
    await releaseConversation(adminToken, selectedId, agentId);
    setFilter("queued");
    refreshContext(selectedId);
    refreshList();
  };
  const doResolve = async () => {
    if (!selectedId) return;
    await resolveConversation(adminToken, selectedId, agentId);
    setSelectedId("");
    setContext(null);
    setFilter("queued");
    refreshList();
  };

  const queuedCount = stats.counts.queued ?? 0;

  return (
    <div className="console">
      {/* Left: queue */}
      <aside className="console-queue">
        <div className="console-id">
          <label>
            <span>坐席ID</span>
            <input value={agentId} onChange={(e) => setAgentId(e.target.value)} />
          </label>
          <label>
            <span>姓名</span>
            <input value={agentName} onChange={(e) => setAgentName(e.target.value)} />
          </label>
        </div>
        <div className="console-stats">
          <span className={queuedCount > 0 ? "pill alert" : "pill"}>排队 {queuedCount}</span>
          <span className="pill">人工 {stats.counts.human ?? 0}</span>
          <span className="pill">在线坐席 {stats.online_agents}</span>
        </div>
        {fb && (
          <div className="console-stats">
            <span className="pill">
              满意度 ⭐{fb.ratings.avg_stars || "-"}（{fb.ratings.count}）
            </span>
            <span className="pill">
              👍 {fb.messages.satisfaction != null ? `${Math.round(fb.messages.satisfaction * 100)}%` : "-"}
              （{fb.messages.total}）
            </span>
          </div>
        )}
        <div className="queue-filters">
          {["queued", "human", "all"].map((f) => (
            <button key={f} className={filter === f ? "chip active" : "chip"} onClick={() => setFilter(f)}>
              {f === "queued" ? "排队" : f === "human" ? "人工中" : "全部"}
            </button>
          ))}
        </div>
        <div className="queue-list">
          {conversations.map((c) => (
            <button
              key={c.conversation_id}
              className={`queue-item ${selectedId === c.conversation_id ? "active" : ""}`}
              onClick={() => setSelectedId(c.conversation_id)}
            >
              <div className="queue-item-top">
                <span className="qcust">{c.customer_id}</span>
                <span className={`prio ${PRIO_CLASS[c.priority] ?? "medium"}`}>P{c.priority}</span>
              </div>
              <div className="queue-item-sub">
                <span className={`sbadge ${c.status}`}>{statusBadge(c.status)}</span>
                {c.escalation_reason && c.escalation_reason !== "none" && (
                  <span className="reason">{REASON_LABEL[c.escalation_reason] ?? c.escalation_reason}</span>
                )}
              </div>
              <div className="queue-item-time">{timeago(c.last_message_at)}</div>
            </button>
          ))}
          {conversations.length === 0 && <p className="muted">暂无会话</p>}
        </div>
      </aside>

      {/* Center: chat */}
      <main className="console-chat">
        {!conv ? (
          <div className="console-empty">← 从左侧选择一个会话</div>
        ) : (
          <>
            <div className="console-chat-head">
              <div>
                <strong>{conv.customer_id}</strong>{" "}
                <span className={`sbadge ${conv.status}`}>{statusBadge(conv.status)}</span>
                {conv.escalation_reason && conv.escalation_reason !== "none" && (
                  <span className="reason">
                    · {REASON_LABEL[conv.escalation_reason] ?? conv.escalation_reason}
                  </span>
                )}
              </div>
              <div className="console-actions">
                {conv.status === "queued" && (
                  <button
                    className="primary"
                    onClick={doClaim}
                    disabled={claimingId === conv.conversation_id}
                  >
                    {claimingId === conv.conversation_id ? "接入中..." : "认领接入"}
                  </button>
                )}
                {conv.status === "human" && (
                  <>
                    <button onClick={doRelease}>释放回AI</button>
                    <button onClick={doResolve}>结束会话</button>
                  </>
                )}
              </div>
            </div>
            {actionError && <div className="console-alert">{actionError}</div>}

            <div className="console-body" ref={bodyRef}>
              {context?.messages.map((m) => (
                <div key={m.id} className={`cmsg ${m.role}`}>
                  {m.role === "system" ? (
                    <div className="cmsg-system">{m.content}</div>
                  ) : (
                    <div className="cmsg-bubble">
                      <div className="cmsg-sender">
                        {m.role === "customer" ? "用户" : m.role === "agent" ? m.sender : "AI"}
                      </div>
                      {m.content}
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div className="console-composer">
              <textarea
                value={draft}
                placeholder={conv.status === "human" ? "输入回复…" : "认领后可回复"}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) doSend();
                }}
              />
              <div className="composer-side">
                <button onClick={doSuggest}>✨ AI建议</button>
                <button className="primary" onClick={doSend} disabled={conv.status !== "human"}>
                  发送
                </button>
              </div>
            </div>
          </>
        )}
      </main>

      {/* Right: customer 360 */}
      <aside className="console-context">
        <h3>客户 360</h3>
        {!context ? (
          <p className="muted">选择会话查看</p>
        ) : (
          <>
            <div className="ctx-card">
              <div className="ctx-title">客户</div>
              <div>{context.customer.customer_id}</div>
              {context.conversation.csat != null && (
                <div className="csat-line">本次评价：⭐ {context.conversation.csat} / 5</div>
              )}
            </div>
            <div className="ctx-card">
              <div className="ctx-title">历史订单 ({context.customer.orders.length})</div>
              {context.customer.orders.map((o) => (
                <div key={o.order_id} className="ctx-order">
                  <span>{o.order_id}</span>
                  <span className="sbadge">{o.status}</span>
                  <span className="price">¥{o.total.toFixed(0)}</span>
                </div>
              ))}
              {context.customer.orders.length === 0 && <p className="muted">无</p>}
            </div>
            <div className="ctx-card">
              <div className="ctx-title">PAHF 记忆画像 ({context.customer.memories.length})</div>
              {context.customer.memories.map((m) => (
                <div key={m.id} className="ctx-memory">
                  {m.text}
                </div>
              ))}
              {context.customer.memories.length === 0 && <p className="muted">暂无记忆</p>}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
