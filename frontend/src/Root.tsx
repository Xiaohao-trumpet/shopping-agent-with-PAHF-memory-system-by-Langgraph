import { useState } from "react";
import Storefront from "./Storefront";
import AgentConsole from "./AgentConsole";
import App from "./App";

type View = "store" | "console" | "debug";

const TABS: Array<{ key: View; label: string }> = [
  { key: "store", label: "🛒 商城" },
  { key: "console", label: "🎧 坐席工作台" },
  { key: "debug", label: "🛠️ 调试台" },
];

export default function Root() {
  const [view, setView] = useState<View>("store");
  return (
    <div className="root">
      <nav className="root-nav">
        <span className="root-logo">ServiceBot · PAHF</span>
        <div className="root-tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={view === t.key ? "root-tab active" : "root-tab"}
              onClick={() => setView(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </nav>
      <div className="root-view">
        {view === "store" && <Storefront />}
        {view === "console" && <AgentConsole />}
        {view === "debug" && <App />}
      </div>
    </div>
  );
}
