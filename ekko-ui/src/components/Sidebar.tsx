import "./Sidebar.css";import { useEffect, useState } from "react";
import {
  BookOpen, Brain, ChartColumn, Cpu, Globe, House, Keyboard, LifeBuoy, MessageSquare, Mic, PanelLeftClose, PanelLeftOpen, Plug, Route, Settings,
  type LucideIcon,
} from "lucide-react";
import { getVersion } from "@tauri-apps/api/app";
import { restartListener } from "../api/client";
import type { EkkoStatus } from "./TopBar";

export const ICONS = {
  Home: House,
  Usage: ChartColumn,
  Chat: MessageSquare,
  Briefing: BookOpen,
  General: Settings,
  Audio: Mic,
  Models: Cpu,
  Search: Globe,
  Routing: Route,
  Hotkeys: Keyboard,
  Memory: Brain,
  Connections: Plug,
  Help: LifeBuoy,
} satisfies Record<string, LucideIcon>;

export type Section = keyof typeof ICONS;

export const LABELS: Partial<Record<Section, string>> = { Audio: "Audio & Voice" };
export const SETTINGS_TABS: Section[] = ["General", "Audio", "Models", "Search", "Briefing", "Routing", "Hotkeys"];
const GROUPS: { title?: string; items: Section[] }[] = [
  { title: "Activity", items: ["Home", "Usage", "Chat", "Briefing", "Memory", "Connections"] },
  { items: ["Help"] },
];

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
  onOpenSettings: () => void;
  settingsOpen: boolean;
}

function Sidebar({ active, status = "idle", onSelect, onOpenSettings, settingsOpen }: SidebarProps) {
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
        {!collapsed && (
          <div className="sidebar__brand">
            ekko
          </div>
        )}
        <button
          type="button"
          className="sidebar__toggle"
          aria-label={collapsed ? "Expand menu" : "Collapse menu"}
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>
      {GROUPS.map((g) => (
        <div key={g.title ?? "foot"} className={g.title ? "sidebar__group" : "sidebar__group sidebar__group--foot"}>
          {g.title && <div className="sidebar__heading">{g.title}</div>}
          <ul className="sidebar__list">
            {g.items.map((section) => {
              const Icon = ICONS[section];
              return (
                <li key={section}>
                  <button
                    type="button"
                    className={`sidebar__item${section === active && !settingsOpen ? " sidebar__item--active" : ""}`}
                    title={LABELS[section] ?? section}
                    onClick={() => onSelect(section)}
                  >
                    <Icon size={16} />
                    <span className="sidebar__label">{LABELS[section] ?? section}</span>
                  </button>
                </li>
              );
            })}
            {!g.title && (
              <li>
                <button
                  type="button"
                  className={`sidebar__item${settingsOpen ? " sidebar__item--active" : ""}`}
                  title="Settings"
                  onClick={onOpenSettings}
                >
                  <Settings size={16} />
                  <span className="sidebar__label">Settings</span>
                </button>
              </li>
            )}
          </ul>
        </div>
      ))}
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
