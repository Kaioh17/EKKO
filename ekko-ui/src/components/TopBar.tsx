import "./TopBar.css";
import { useEffect, useState } from "react";
import { Minus, Moon, Search, Square, Sun, X } from "lucide-react";
import { getGeneralOverview, sendStop } from "../api/client";
import { getCurrentWindow } from "@tauri-apps/api/window";

export type EkkoStatus = "idle" | "listening" | "processing" | "speaking" | "error";

const STATUS_LABEL: Record<EkkoStatus, string> = {
  idle: "Idle",
  listening: "Listening",
  processing: "Processing",
  speaking: "Speaking",
  error: "Error",
};

interface TopBarProps {
  status?: EkkoStatus;
  section?: string;
  onWake?: () => Promise<unknown>;
  query?: string;
  onQuery?: (q: string) => void;
  advanced?: boolean;
  onAdvanced?: (v: boolean) => void;
}

const DESCRIPTIONS: Record<string, string> = {
  Home: "Your month at a glance.",
  Usage: "What ekko has done, and what it cost.",
  Chat: "Type to ekko when you can’t talk out loud.",
  Briefing: "What goes into your daily briefing, and when.",
  General: "How ekko starts, hears you, and sounds.",
  Connections: "Accounts, agents and tools ekko can use.",
};

function TopBar({ status = "idle", section = "", onWake, query = "", onQuery, advanced = false, onAdvanced }: TopBarProps) {
  const [waking, setWaking] = useState(false);
  const [wakeKey, setWakeKey] = useState("");
  useEffect(() => {
    getGeneralOverview()
      .then((o) => {
        const k = o.hotkeys.find((h) => /wake/i.test(h.name) && h.enabled);
        if (k) setWakeKey(k.chord.split("+").map((x) => x[0].toUpperCase() + x.slice(1)).join("+"));
      })
      .catch(() => {});
  }, []);
  const win = getCurrentWindow();
  const [dark, setDark] = useState(() => localStorage.getItem("theme") === "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);

  const active = status !== "idle" && status !== "error";

  async function handleWake() {
    if ((!active && !onWake) || waking) return;
    setWaking(true);
    try {
      await (active ? sendStop() : onWake!());
    } catch {
      // Best effort -- no listener process running is a normal state,
      // not something worth surfacing as a UI error.
    } finally {
      setWaking(false);
    }
  }

  return (
    <header className="topbar" data-tauri-drag-region>
      <div className="topbar__title">
        {section === "General" ? "General & Voice" : section}
        {DESCRIPTIONS[section] && <span className="topbar__desc">{DESCRIPTIONS[section]}</span>}
      </div>
      <div className="topbar__right">
        {section === "General" && onQuery && (
          <label className="topbar__search">
            <Search size={14} />
            <input value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Search settings…" />
            {query && (
              <button type="button" aria-label="Clear search" onClick={() => onQuery("")}>
                <X size={13} />
              </button>
            )}
          </label>
        )}
        {section === "Usage" && onAdvanced && (
          <div className="topbar__cell topbar__seg">
            <button type="button" className={advanced ? "" : "is-on"} onClick={() => onAdvanced(false)}>Simple</button>
            <button type="button" className={advanced ? "is-on" : ""} onClick={() => onAdvanced(true)}>Advanced</button>
          </div>
        )}
        <div className={`topbar__cell topbar__cell--${status}`}>
          <span className={`status-dot status-dot--${status}`} />
          {STATUS_LABEL[status]}
        </div>
        <button
          type="button"
          className="topbar__wake"
          title={active ? "Stop ekko" : "Manually trigger the wake word"}
          onClick={handleWake}
          disabled={waking}
        >
          {active ? "Stop" : waking ? "Waking…" : "Wake"}
          {!active && wakeKey && <span className="topbar__key">{wakeKey}</span>}
        </button>
        <div className="topbar__win">
          <button type="button" aria-label="Toggle theme" title="Toggle theme" onClick={() => setDark((d) => !d)}>
            {dark ? <Sun size={14} /> : <Moon size={14} />}
          </button>
          <button type="button" aria-label="Minimize" onClick={() => void win.minimize()}><Minus size={14} /></button>
          <button type="button" aria-label="Maximize" onClick={() => void win.toggleMaximize()}><Square size={11} /></button>
          <button type="button" aria-label="Close" onClick={() => void win.close()}><X size={14} /></button>
        </div>
      </div>
    </header>
  );
}

export default TopBar;
