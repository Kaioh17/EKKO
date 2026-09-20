import "./Panels.css";

interface Hotkey {
  name: string;
  chord: string;
  description: string;
}

const HOTKEYS: Hotkey[] = [
  {
    name: "Manual wake",
    chord: "Ctrl+Alt+W",
    description: "Jump straight to active listening, skipping the wake word and speaker verification.",
  },
  {
    name: "Hard stop",
    chord: "Ctrl+Alt+Q",
    description: "Cut off any in-progress TTS audio and abort the current action immediately.",
  },
];

function HotkeysPanel() {
  return (
    <div className="panel">
      <h2>Hotkeys</h2>
      <ul className="panel__list">
        {HOTKEYS.map((hotkey) => (
          <li className="panel__list-item" key={hotkey.name}>
            <div className="panel__field">
              <span className="panel__value">{hotkey.name}</span>
              <span className="panel__label">{hotkey.description}</span>
            </div>
            <span className="panel__value panel__mono">{hotkey.chord}</span>
          </li>
        ))}
      </ul>
      <p className="panel__empty">Fixed for now, not yet rebindable from here.</p>
    </div>
  );
}

export default HotkeysPanel;
