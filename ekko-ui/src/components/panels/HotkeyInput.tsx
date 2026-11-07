import { useEffect, useState } from "react";
import { chordFromEvent, chordProblem, formatChord } from "../../hooks/chord";

/** A chord drawn as key caps. */
export function KeyCaps({ chord }: { chord: string }) {
  return (
    <span className="keycaps">
      {formatChord(chord).map((k) => (
        <kbd key={k} className="keycap">{k}</kbd>
      ))}
    </span>
  );
}

/** Click, then press the combination you want. Esc cancels. A chord the listener
 *  would refuse is explained here instead of failing at save. */
export function HotkeyInput({
  value,
  onChange,
  labelId,
  defaultValue,
}: {
  value: string;
  onChange: (chord: string) => void;
  labelId: string;
  defaultValue: string;
}) {
  const [recording, setRecording] = useState(false);
  const [held, setHeld] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault(); // Alt would open a menu, Tab would move focus away
      e.stopPropagation();
      if (e.code === "Escape") {
        setRecording(false);
        setHeld([]);
        return;
      }
      const { chord, held: down } = chordFromEvent(e);
      setHeld(down);
      if (!chord) return; // only modifiers so far
      const why = chordProblem(chord);
      setProblem(why);
      if (!why) onChange(chord);
      setRecording(false);
      setHeld([]);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [recording, onChange]);

  return (
    <span className="srow__stack">
      <button
        type="button"
        className={`hotkey${recording ? " is-recording" : ""}`}
        aria-labelledby={labelId}
        onClick={() => {
          setProblem(null);
          setRecording(true);
        }}
        onBlur={() => setRecording(false)}
      >
        {recording ? (held.length ? `${formatChord(held.join("+")).join(" + ")} + …` : "Press the keys…") : <KeyCaps chord={value} />}
      </button>
      {recording && <span className="srow__note">Esc to cancel</span>}
      {!recording && problem && <span className="panel__warning">{problem}</span>}
      {!recording && !problem && value !== defaultValue && (
        <button type="button" className="panel__link" onClick={() => onChange(defaultValue)}>
          Back to {formatChord(defaultValue).join("+")}
        </button>
      )}
    </span>
  );
}
