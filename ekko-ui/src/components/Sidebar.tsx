import "./Sidebar.css";
import {
  CircleHelp,
  MessageCircle,
  Newspaper,
  Settings,
  Volume2,
  Cpu,
  Route,
  Keyboard,
  Database,
  RotateCw,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { restartListener } from "../api/client";

const SECTIONS = [
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

const SECTION_ICONS: Record<Section, LucideIcon> = {
  Chat: MessageCircle,
  Briefing: Newspaper,
  General: Settings,
  Audio: Volume2,
  Models: Cpu,
  Routing: Route,
  Hotkeys: Keyboard,
  Memory: Database,
  Help: CircleHelp,
};

interface SidebarProps {
  active: Section;
  onSelect: (section: Section) => void;
}

function Sidebar({ active, onSelect }: SidebarProps) {
  const [restarting, setRestarting] = useState(false);

  function restart() {
    setRestarting(true);
    restartListener()
      .catch(() => {})
      .finally(() => setTimeout(() => setRestarting(false), 10000)); // restart.ps1 takes ~10s
  }

  return (
    <nav className="sidebar">
      <div className="sidebar__title">
        <img className="sidebar__logo" src="/ekko-mark.png" alt="" />
        EKKO
      </div>
      <ul className="sidebar__list">
        {SECTIONS.map((section) => {
          const Icon = SECTION_ICONS[section];
          return (
            <li key={section}>
              <button
                type="button"
                className={`sidebar__item${section === active ? " sidebar__item--active" : ""}`}
                onClick={() => onSelect(section)}
              >
                <Icon size={17} strokeWidth={1.8} />
                {section}
              </button>
            </li>
          );
        })}
      </ul>
      <button type="button" className="sidebar__item sidebar__restart" onClick={restart} disabled={restarting}>
        <RotateCw size={17} strokeWidth={1.8} className={restarting ? "sidebar__spin" : undefined} />
        {restarting ? "Restarting…" : "Restart"}
      </button>
    </nav>
  );
}

export default Sidebar;
