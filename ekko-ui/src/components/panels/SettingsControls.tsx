import "./Panels.css";
import { useEffect, useState, type InputHTMLAttributes } from "react";

// Holds keystrokes locally; commits on blur/Enter so half-typed values ("0.") never reach the draft.
export function DraftInput({
  value,
  onCommit,
  ...props
}: { value: string; onCommit: (raw: string) => void } & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <input
      {...props}
      className="panel__input"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onCommit(draft)}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
    />
  );
}

interface SettingsActionsProps {
  dirty: boolean;
  busy: boolean;
  error: string | null;
  onSave: () => void;
  onReset: () => void;
}

// Save + two-click "Reset to defaults" (no native confirm(): Tauri's webview doesn't reliably show it).
export function SettingsActions({ dirty, busy, error, onSave, onReset }: SettingsActionsProps) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="panel__actions">
      <button type="button" className="panel__button panel__button--primary" disabled={!dirty || busy} onClick={onSave}>
        {busy ? "Saving…" : dirty ? "Save changes" : "Saved"}
      </button>
      <button
        type="button"
        className={`panel__button${confirming ? " panel__button--danger" : ""}`}
        disabled={busy}
        onBlur={() => setConfirming(false)}
        onClick={() => {
          if (!confirming) return setConfirming(true);
          setConfirming(false);
          onReset();
        }}
      >
        {confirming ? "Click again to reset all" : "Reset to defaults"}
      </button>
      {dirty && !busy && <span className="panel__label">Unsaved changes</span>}
      {error && <span className="panel__warning">{error}</span>}
    </div>
  );
}
