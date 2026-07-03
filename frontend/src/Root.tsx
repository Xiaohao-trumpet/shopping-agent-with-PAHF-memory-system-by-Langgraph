import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import Storefront from "./Storefront";
import AgentConsole from "./AgentConsole";
import App from "./App";
import AdminDashboard from "./AdminDashboard";
import { loginAdmin, loginCustomer, logoutAdmin } from "./shopApi";

type View = "store" | "console" | "admin" | "debug";
type PortalRole = "customer" | "merchant";

interface PortalSession {
  role: PortalRole;
  username: string;
  displayName: string;
  customerId?: string;
  agentId?: string;
  agentName?: string;
  adminToken?: string;
}

const PORTAL_SESSION_KEY = "servicebot_portal_session_v1";
const ADMIN_TOKEN_KEY = "servicebot_admin_token";

const CUSTOMER_TABS: Array<{ key: View; label: string }> = [
  { key: "store", label: "🛒 商城" },
];

const MERCHANT_TABS: Array<{ key: View; label: string }> = [
  { key: "console", label: "🎧 坐席工作台" },
  { key: "admin", label: "📊 后台管理" },
  { key: "debug", label: "🛠️ 调试台" },
];

function readSession(): PortalSession | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(PORTAL_SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as PortalSession;
  } catch {
    window.localStorage.removeItem(PORTAL_SESSION_KEY);
    return null;
  }
}

export default function Root() {
  const [session, setSession] = useState<PortalSession | null>(() => readSession());
  const [view, setView] = useState<View>(() => {
    const current = readSession();
    return current?.role === "merchant" ? "admin" : "store";
  });

  const tabs = useMemo(
    () => (session?.role === "merchant" ? MERCHANT_TABS : CUSTOMER_TABS),
    [session?.role]
  );

  useEffect(() => {
    if (!session) return;
    if (!tabs.some((tab) => tab.key === view)) {
      setView(tabs[0].key);
    }
  }, [session, tabs, view]);

  const saveSession = (next: PortalSession) => {
    window.localStorage.setItem(PORTAL_SESSION_KEY, JSON.stringify(next));
    setSession(next);
    setView(next.role === "merchant" ? "admin" : "store");
  };

  const handleLogout = async () => {
    const token = session?.adminToken || window.localStorage.getItem(ADMIN_TOKEN_KEY) || "";
    if (token) {
      await logoutAdmin(token).catch(() => undefined);
    }
    window.localStorage.removeItem(PORTAL_SESSION_KEY);
    window.localStorage.removeItem(ADMIN_TOKEN_KEY);
    setSession(null);
    setView("store");
  };

  if (!session) {
    return <LoginScreen onLogin={saveSession} />;
  }

  return (
    <div className="root">
      <nav className="root-nav">
        <div className="root-brand-block">
          <span className="root-logo">ServiceBot · PAHF</span>
          <span className="root-role">
            {session.role === "customer" ? "顾客端" : "商家客服端"} · {session.displayName}
          </span>
        </div>
        <div className="root-tabs">
          {tabs.map((t) => (
            <button
              key={t.key}
              className={view === t.key ? "root-tab active" : "root-tab"}
              onClick={() => setView(t.key)}
            >
              {t.label}
            </button>
          ))}
          <button className="root-logout" onClick={() => void handleLogout()}>
            退出登录
          </button>
        </div>
      </nav>
      <div className="root-view">
        {view === "store" && (
          <Storefront
            initialCustomerId={session.customerId ?? session.username}
            lockedCustomerId={session.role === "customer"}
            customerName={session.displayName}
          />
        )}
        {view === "console" && (
          <AgentConsole
            initialAgentId={session.agentId ?? "agent-1"}
            initialAgentName={session.agentName ?? session.displayName}
          />
        )}
        {view === "admin" && <AdminDashboard />}
        {view === "debug" && <App />}
      </div>
    </div>
  );
}

function LoginScreen({ onLogin }: { onLogin: (session: PortalSession) => void }) {
  const [role, setRole] = useState<PortalRole>("customer");
  const [username, setUsername] = useState("c9001");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const switchRole = (nextRole: PortalRole) => {
    setRole(nextRole);
    setError("");
    if (nextRole === "customer") {
      setUsername("c9001");
      setPassword("");
    } else {
      setUsername("admin");
      setPassword("");
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (role === "customer") {
        const session = await loginCustomer(username.trim(), password);
        onLogin({
          role: "customer",
          username: session.customer.customer_id,
          displayName: session.customer.name,
          customerId: session.customer.customer_id,
        });
        return;
      }

      const session = await loginAdmin(username.trim(), password);
      window.localStorage.setItem(ADMIN_TOKEN_KEY, session.access_token);
      onLogin({
        role: "merchant",
        username: session.user.username,
        displayName: session.user.display_name || "商家客服",
        agentId: "agent-1",
        agentName: session.user.display_name || "商家客服",
        adminToken: session.access_token,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="portal-login-shell">
      <section className="portal-login-hero">
        <div className="portal-login-copy">
          <p className="admin-kicker">ServiceBot SaaS</p>
          <h1>电商售后客服与评价分析平台</h1>
          <p>
            顾客进入商城咨询商品和售后，商家客服进入后台处理会话、查看评价和运营数据。
          </p>
        </div>
        <form className="portal-login-card" onSubmit={submit}>
          <div className="portal-role-switch" aria-label="选择登录角色">
            <button
              type="button"
              className={role === "customer" ? "active" : ""}
              onClick={() => switchRole("customer")}
            >
              顾客账号
            </button>
            <button
              type="button"
              className={role === "merchant" ? "active" : ""}
              onClick={() => switchRole("merchant")}
            >
              商家/客服
            </button>
          </div>

          <label>
            账号
            <input value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label>
            密码
            <input
              value={password}
              type="password"
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error && <div className="admin-alert">{error}</div>}
          <button className="primary wide" disabled={loading}>
            {loading ? "登录中..." : "登录系统"}
          </button>
          <div className="portal-demo-accounts">
            <span>账号由后端种子数据与管理员库校验</span>
          </div>
        </form>
      </section>
    </main>
  );
}
