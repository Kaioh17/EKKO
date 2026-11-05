import "./Panels.css";
import { useEffect, useState } from "react";
import { Info, X } from "lucide-react";
import { getArchive, getDayLog, getLongTermMemory, getReview, getWindow } from "../../api/client";
import ActiveMemory from "./ActiveMemory";
import TodayMemory from "./TodayMemory";
import ArchiveMemory from "./ArchiveMemory";
import LongTermMemory from "./LongTermMemory";
import MemoryGuide from "./MemoryGuide";
import ReviewQueue from "./ReviewQueue";

// Ordered like the prompt, newest context first: the live conversation and
// today's summary (Now), the raw last 24h (Today), past days (Archive), facts.
const TABS = ["Now", "Today", "Archive", "Long Term", "Review"] as const;
const VIEWS = { Now: ActiveMemory, Today: TodayMemory, Archive: ArchiveMemory, "Long Term": LongTermMemory, Review: ReviewQueue };
type Tab = (typeof TABS)[number];

// Small badge per tab; blank until its fetch lands (or if it fails).
function useCounts(): Partial<Record<Tab, string>> {
  const [counts, setCounts] = useState<Partial<Record<Tab, string>>>({});
  useEffect(() => {
    const set = (tab: Tab, value: string) => setCounts((c) => ({ ...c, [tab]: value }));
    getWindow().then((w) => set("Now", String(w.turns.length))).catch(() => {});
    getDayLog().then((l) => set("Today", String(l.turns.length))).catch(() => {});
    getArchive().then((a) => set("Archive", `${a.entries.length}d`)).catch(() => {});
    getReview().then((r) => set("Review", String(r.items.length))).catch(() => {});
    getLongTermMemory()
      .then((m) => set("Long Term", String(Object.values(m.sections).reduce((n, s) => n + s.length, 0))))
      .catch(() => {});
  }, []);
  return counts;
}

function MemoryPanel() {
  const [tab, setTab] = useState<Tab>("Now");
  const [help, setHelp] = useState(false);
  const counts = useCounts();
  const View = VIEWS[tab];

  return (
    <div className="panel panel--memory">
      <div className="panel__tabs">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            className={`panel__tab${t === tab ? " panel__tab--active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
            {counts[t] && <span className="panel__tab-count">{counts[t]}</span>}
          </button>
        ))}
      </div>
      <View />
      <button
        type="button"
        className="panel__info"
        aria-expanded={help}
        aria-label="How memory works"
        title="How memory works"
        onClick={() => setHelp(!help)}
      >
        {help ? <X size={18} /> : <Info size={18} />}
      </button>
      {help && <MemoryGuide />}
    </div>
  );
}

export default MemoryPanel;
