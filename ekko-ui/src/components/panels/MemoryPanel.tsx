import "./Panels.css";
import { useState } from "react";
import ActiveMemory from "./ActiveMemory";
import TodayMemory from "./TodayMemory";
import ArchiveMemory from "./ArchiveMemory";
import LongTermMemory from "./LongTermMemory";

// Ordered like the prompt, newest context first: the live conversation and
// today's summary (Now), the raw last 24h (Today), past days (Archive), facts.
const TABS = ["Now", "Today", "Archive", "Long Term"] as const;
const VIEWS = { Now: ActiveMemory, Today: TodayMemory, Archive: ArchiveMemory, "Long Term": LongTermMemory };
type Tab = (typeof TABS)[number];

function MemoryPanel() {
  const [tab, setTab] = useState<Tab>("Now");
  const View = VIEWS[tab];

  return (
    <div className="panel">
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
      <View />
    </div>
  );
}

export default MemoryPanel;
