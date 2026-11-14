import "./Panels.css";
import { useEffect, useState } from "react";
import { getGeneralOverview } from "../../api/client";
import { KeyCaps } from "./HotkeyInput";

const HOTKEYS: { name: string; chord: string; description: string }[] = [
  {
    name: "Hold to talk",
    chord: "ctrl+alt+space",
    description: "Optional. Hold it, speak, let go: no wake word and no waiting for a pause. Turn it on and choose your keys under Audio & Voice.",
  },
  {
    name: "Manual wake",
    chord: "ctrl+alt+w",
    description: "Jump straight to active listening, skipping the wake word and speaker verification.",
  },
  {
    name: "Hard stop",
    chord: "ctrl+alt+q",
    description: "Cut off any in-progress TTS audio and abort the current action immediately.",
  },
];

function HotkeysPanel() {
  // What's actually configured: the chord (hold to talk can be rebound) and whether each is on.
  const [live, setLive] = useState<Record<string, { chord: string; enabled: boolean }>>({});
  useEffect(() => {
    getGeneralOverview()
      .then((o) => setLive(Object.fromEntries(o.hotkeys.map((h) => [h.name, h]))))
      .catch(() => undefined);
  }, []);

  return (
    <div className="panel">
      <ul className="panel__list">
        {HOTKEYS.map((hotkey) => {
          const now = live[hotkey.name];
          return (
            <li className="panel__list-item" key={hotkey.name}>
              <div className="panel__field">
                <span className="panel__value">
                  {hotkey.name} {now && !now.enabled && <span className="panel__badge">Off</span>}
                </span>
                <span className="panel__label">{hotkey.description}</span>
              </div>
              <KeyCaps chord={now?.chord ?? hotkey.chord} />
            </li>
          );
        })}
      </ul>
      <p className="panel__empty">Manual wake and Hard stop are fixed. Hold to talk can be turned on and rebound under General &amp; Voice.</p>
    </div>
  );
}

export default HotkeysPanel;
