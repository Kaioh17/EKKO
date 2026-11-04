import "./TopBar.css";
import { useEffect, useState } from "react";
import { Minus, Square, X } from "lucide-react";
import { getGeneralOverview } from "../api/client";
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
}

const fmtNow = () =>
  new Date().toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).replace(",", "").replace(/(\d{2}:\d{2})$/, "· $1");

function TopBar({ status = "idle", section = "", onWake }: TopBarProps) {
  const [waking, setWaking] = useState(false);
  const [now, setNow] = useState(fmtNow);
  useEffect(() => {
    const id = setInterval(() => setNow(fmtNow()), 15000);
    return () => clearInterval(id);
  }, []);
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

  async function handleWake() {
    if (!onWake || waking) return;
    setWaking(true);
    try {
      await onWake();
    } catch {
      // Best effort -- no listener process running is a normal state,
      // not something worth surfacing as a UI error.
    } finally {
      setWaking(false);
    }
  }

  return (
    <header className="topbar" data-tauri-drag-region>
      <div className="topbar__title">{section}</div>
      <div className="topbar__right">
        <span className="topbar__clock">{now}</span>
        <div className="topbar__cell">
          <span className={`status-dot status-dot--${status}`} />
          {STATUS_LABEL[status]}
        </div>
        <button
          type="button"
          className="topbar__wake"
          title="Manually trigger the wake word"
          onClick={handleWake}
          disabled={waking}
        >
          {waking ? "Waking…" : "Wake"}
          {wakeKey && <span className="topbar__key">{wakeKey}</span>}
        </button>
        <div className="topbar__win">
          <button type="button" aria-label="Minimize" onClick={() => void win.minimize()}><Minus size={14} /></button>
          <button type="button" aria-label="Maximize" onClick={() => void win.toggleMaximize()}><Square size={11} /></button>
          <button type="button" aria-label="Close" onClick={() => void win.close()}><X size={14} /></button>
        </div>
      </div>
    </header>
  );
}

export default TopBar;
