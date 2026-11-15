import "./Sidebar.css";import { useEffect, useState } from "react";
import {
  ArrowLeft, BookOpen, Brain, CalendarDays, ChartColumn, Cpu, Globe, House, Keyboard, LifeBuoy, MessageSquare, Mic, PanelLeftClose, PanelLeftOpen, Plug, Route, Settings,
  type LucideIcon,
} from "lucide-react";
import { getVersion } from "@tauri-apps/api/app";
import { getChatDays, restartListener, type ChatDay } from "../api/client";
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
export const SETTINGS_TABS: Section[] = ["General", "Audio", "Models", "Search", "Briefing", "Chat", "Routing", "Hotkeys"];
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
  chatDay: string | null; // day open in the Chat panel; null = today
  onChatDay: (day: string | null) => void;
}

const dayLabel = (day: string) =>
  new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

function Sidebar({ active, status = "idle", onSelect, onOpenSettings, settingsOpen, chatDay, onChatDay }: SidebarProps) {
  const [restarting, setRestarting] = useState(false);
  const [version, setVersion] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  // Opening Chat swaps the Activity group for the chat history menu; Back returns to the regular one.
  const [chatMenu, setChatMenu] = useState(active === "Chat");
  const [days, setDays] = useState<ChatDay[]>([]);

  useEffect(() => {
    getVersion().then(setVersion).catch(() => {});
  }, []);

  useEffect(() => setChatMenu(active === "Chat"), [active]);

  useEffect(() => {
    if (!chatMenu) return;
    const today = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in local time, like the backend
    getChatDays().then((all) => setDays(all.filter((d) => d.day < today))).catch(() => setDays([]));
  }, [chatMenu]);

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
      {GROUPS.map((g) => g.title && chatMenu && !settingsOpen ? (
        <div key="chat-history" className="sidebar__group">
          <button type="button" className="sidebar__item" title="Back to menu" onClick={() => setChatMenu(false)}>
            <ArrowLeft size={16} />
            <span className="sidebar__label">Back</span>
          </button>
          <div className="sidebar__heading">Chat history</div>
          <ul className="sidebar__list">
            {[{ day: null, label: "Today" }, ...days.map((d) => ({ day: d.day, label: dayLabel(d.day) }))].map(({ day, label }) => (
              <li key={day ?? "today"}>
                <button
                  type="button"
                  className={`sidebar__item${day === chatDay ? " sidebar__item--active" : ""}`}
                  title={label}
                  onClick={() => onChatDay(day)}
                >
                  <CalendarDays size={16} />
                  <span className="sidebar__label">{label}</span>
                </button>
              </li>
            ))}
          </ul>
          {!days.length && <p className="sidebar__empty">Daily chat history will be saved here</p>}
        </div>
      ) : (
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
                    onClick={() => {
                      if (section === "Chat") setChatMenu(true); // already on Chat after Back: `active` won't change
                      onSelect(section);
                    }}
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
