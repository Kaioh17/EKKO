import "./Sidebar.css";
import { useEffect, useState } from "react";
import {
  BookOpen, Brain, Cpu, Keyboard, LayoutDashboard, LifeBuoy, MessageSquare, Mic, PanelLeftClose, PanelLeftOpen, Route, Settings,
  type LucideIcon,
} from "lucide-react";
import { getVersion } from "@tauri-apps/api/app";
import { restartListener } from "../api/client";
import type { EkkoStatus } from "./TopBar";

const ICONS = {
  Overview: LayoutDashboard,
  General: Settings,
  Chat: MessageSquare,
  Briefing: BookOpen,
  Audio: Mic,
  Models: Cpu,
  Routing: Route,
  Hotkeys: Keyboard,
  Memory: Brain,
  Help: LifeBuoy,
} satisfies Record<string, LucideIcon>;

export type Section = keyof typeof ICONS;
const SECTIONS = Object.keys(ICONS) as Section[];

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
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    getVersion().then(setVersion).catch(() => {});
  }, []);

  function restart() {
    setRestarting(true);
    restartListener()
      .catch(() => {})
      .finally(() => setTimeout(() => setRestarting(false), 10000)); // restart.ps1 takes ~10s
  }

  return (
    <nav className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`}>
      <div className="sidebar__title">
        {!collapsed && <div className="sidebar__brand">ekko</div>}
        <button
          type="button"
          className="sidebar__toggle"
          aria-label={collapsed ? "Expand menu" : "Collapse menu"}
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>
      <ul className="sidebar__list">
        {SECTIONS.map((section) => {
          const Icon = ICONS[section];
          return (
          <li key={section}>
            <button
              type="button"
              className={`sidebar__item${section === active ? " sidebar__item--active" : ""}`}
              onClick={() => onSelect(section)}
            >
              <Icon size={16} />
              <span className="sidebar__label">{section}</span>
            </button>
          </li>
          );
        })}
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
