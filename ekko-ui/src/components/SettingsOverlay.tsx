import "./SettingsOverlay.css";
import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { ICONS, LABELS, SETTINGS_TABS, type Section } from "./Sidebar";

interface Props {
  tab: Section;
  onTab: (tab: Section) => void;
  onClose: () => void;
  children: ReactNode;
}

function SettingsOverlay({ tab, onTab, onClose, children }: Props) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  return (
    <div className="sov" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sov__card" role="dialog" aria-modal="true" aria-label="Settings">
        <header className="sov__head">
          <h2>Settings</h2>
          <button type="button" aria-label="Close settings" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="sov__body">
          <nav className="sov__nav">
            {SETTINGS_TABS.map((t) => {
              const Icon = ICONS[t];
              return (
                <button key={t} type="button" className={t === tab ? "is-on" : ""} onClick={() => onTab(t)}>
                  <Icon size={16} />
                  {LABELS[t] ?? t}
                </button>
              );
            })}
          </nav>
          <div className="sov__content">{children}</div>
        </div>
      </div>
    </div>
  );
}

export default SettingsOverlay;
