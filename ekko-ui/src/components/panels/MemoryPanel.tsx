import "./Panels.css";
import { useState } from "react";
import ShortTermMemory from "./ShortTermMemory";
import LongTermMemory from "./LongTermMemory";

const TABS = ["Short Term", "Long Term"] as const;
type Tab = (typeof TABS)[number];

function MemoryPanel() {
  const [tab, setTab] = useState<Tab>("Short Term");

  return (
    <div className="panel">
      <h2>Memory</h2>
      <div className="panel__tabs">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            className={`panel__tab${t === tab ? " panel__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Short Term" ? <ShortTermMemory /> : <LongTermMemory />}
    </div>
  );
}

export default MemoryPanel;
