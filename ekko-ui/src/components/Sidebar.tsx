import "./Sidebar.css";
import { useEffect, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { getGeneralOverview, restartListener } from "../api/client";
import type { EkkoStatus } from "./TopBar";

const SECTIONS = [
  "Overview",
  "General",
  "Chat",
  "Briefing",
  "Audio",
  "Models",
  "Routing",
  "Hotkeys",
  "Memory",
  "Help",
] as const;

export type Section = (typeof SECTIONS)[number];

const STATUS_LABEL: Record<EkkoStatus, string> = {
  idle: "Idle",
  listening: "Listening",
  processing: "Processing",
  speaking: "Speaking",
  error: "Error",
};

interface SidebarProps {
  active: Section;
  status?: EkkoStatus;
  onSelect: (section: Section) => void;
}

function Sidebar({ active, status = "idle", onSelect }: SidebarProps) {
  const [restarting, setRestarting] = useState(false);
  const [version, setVersion] = useState("");
  const [pending, setPending] = useState(0);

  useEffect(() => {
    getVersion().then(setVersion).catch(() => {});
    getGeneralOverview().then((o) => setPending(o.memory.pending)).catch(() => {});
  }, []);

  function restart() {
    setRestarting(true);
    restartListener()
      .catch(() => {})
      .finally(() => setTimeout(() => setRestarting(false), 10000)); // restart.ps1 takes ~10s
  }

  return (
    <nav className="sidebar">
      <div className="sidebar__title">
        <div className="sidebar__brand">
          ekko
        </div>
      </div>
      <ul className="sidebar__list">
        {SECTIONS.map((section) => (
          <li key={section}>
            <button
              type="button"
              className={`sidebar__item${section === active ? " sidebar__item--active" : ""}`}
              onClick={() => onSelect(section)}
            >
              {section}
              {section === "Memory" && pending > 0 && <span className="sidebar__badge">{pending}</span>}
            </button>
          </li>
        ))}
      </ul>
      <div className="sidebar__live">
        <span className={`status-dot status-dot--${status}`} />
        <div className="sidebar__live-text">
          <span>ekko{version && ` v${version}`}</span>
          <span>{STATUS_LABEL[status]}</span>
        </div>
        <button type="button" onClick={restart} disabled={restarting}>
          {restarting ? "Restarting…" : "Restart"}
        </button>
      </div>
    </nav>
  );
}

export default Sidebar;
