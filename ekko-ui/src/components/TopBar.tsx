import "./TopBar.css";
import { useState } from "react";

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
  onWake?: () => Promise<unknown>;
}

function TopBar({ status = "idle", onWake }: TopBarProps) {
  const [waking, setWaking] = useState(false);

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
    <header className="topbar">
      <div className="topbar__actions">
        <div className="topbar__status">
          <span className={`status-dot status-dot--${status}`} />
          <span className="topbar__status-label">{STATUS_LABEL[status]}</span>
        </div>
        <button
          type="button"
          className={`wake-button${waking ? " wake-button--active" : ""}`}
          title="Manually trigger the wake word"
          onClick={handleWake}
          disabled={waking}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="9" y="2" width="6" height="12" rx="3" />
            <path d="M5 11a7 7 0 0 0 14 0" />
            <path d="M12 18v4" />
            <path d="M8 22h8" />
          </svg>
          {waking ? "Waking…" : "Wake"}
        </button>
      </div>
    </header>
  );
}

export default TopBar;
