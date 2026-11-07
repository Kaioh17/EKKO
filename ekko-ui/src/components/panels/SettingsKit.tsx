import "./Settings.css";
import { createContext, useContext, useEffect, useId, useRef, useState, type MutableRefObject, type ReactNode } from "react";

/** What a settings page tells the app shell so tab changes can ask first. */
export interface Guard {
  dirty: boolean;
  save: () => Promise<boolean>;
  discard: () => void;
}
export const GuardContext = createContext<MutableRefObject<Guard | null> | null>(null);

/** The subset of useSettingsForm (or a look-alike) a page needs to save/discard as one unit. */
export interface PageForm {
  dirty: boolean;
  busy: boolean;
  error: string | null;
  save: () => Promise<boolean>;
  discard: () => void;
  reset: () => Promise<boolean>;
}

export function SettingsPage({ forms, children, note = "Saving restarts the listener.", className = "" }: { forms: PageForm[]; children: ReactNode; note?: string; className?: string }) {
  const guard = useContext(GuardContext);
  const [confirming, setConfirming] = useState(false);
  const dirty = forms.some((f) => f.dirty);
  const busy = forms.some((f) => f.busy);
  const error = forms.find((f) => f.error)?.error ?? null;

  const save = async () => {
    for (const f of forms) if (f.dirty && !(await f.save())) return false;
    return true;
  };
  const discard = () => forms.forEach((f) => f.discard());

  // Re-registered every render so the shell always sees the latest drafts.
  useEffect(() => {
    if (guard) guard.current = { dirty, save, discard };
  });
  useEffect(() => () => void (guard && (guard.current = null)), [guard]);

  return (
    <div className={`panel panel--wide sset ${className}`}>
      {children}

      <div className="sset__restore">
        <span>Start over with the recommended settings.</span>
        <button
          type="button"
          className="panel__button"
          disabled={busy}
          onBlur={() => setConfirming(false)}
          onClick={() => {
            if (!confirming) return setConfirming(true);
            setConfirming(false);
            forms.forEach((f) => void f.reset());
          }}
        >
          {confirming ? "Click again to confirm" : "Restore defaults"}
        </button>
      </div>

      {(dirty || error) && (
        <div className="sset__bar" role="status">
          <span className="sset__bar-text">
            {error ? <span className="panel__warning">{error}</span> : (
              <>
                <span className="sset__dot" aria-hidden="true" />
                Unsaved changes. {note}
              </>
            )}
          </span>
          <span className="sset__bar-actions">
            <button type="button" className="panel__button" disabled={busy || !dirty} onClick={discard}>
              Cancel
            </button>
            <button type="button" className="panel__button panel__button--primary" disabled={busy || !dirty} onClick={() => void save()}>
              {busy ? "Saving…" : "Save changes"}
            </button>
          </span>
        </div>
      )}
    </div>
  );
}

export function SettingsSection({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="sset__section">
      <header className="sset__head">
        <h3 className="sset__title">{title}</h3>
        <p className="sset__hint">{hint}</p>
      </header>
      <div className="sset__rows">{children}</div>
    </section>
  );
}

/** One setting: title + hint on the left, control on the right (stacks on narrow screens). */
export function Row({
  title,
  hint,
  note,
  toggle,
  children,
}: {
  title: string;
  hint?: ReactNode;
  note?: ReactNode;
  toggle?: boolean;
  children: (labelId: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className={`srow${toggle ? " srow--toggle" : ""}`}>
      <div className="srow__text">
        <span id={id} className="srow__title">{title}</span>
        {hint && <span className="srow__hint">{hint}</span>}
      </div>
      <div className="srow__control">
        {children(id)}
        {note && <span className="srow__note">{note}</span>}
      </div>
    </div>
  );
}

export function Switch({ labelId, checked, onChange }: { labelId: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="panel__toggle">
      <input type="checkbox" className="panel__toggle-input" aria-labelledby={labelId} checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="panel__toggle-track" />
    </label>
  );
}

export function Segmented<T extends string | number>({
  labelId,
  options,
  value,
  onChange,
}: {
  labelId: string;
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="radiogroup" aria-labelledby={labelId}>
      {options.map((o) => (
        <button key={o.label} type="button" role="radio" aria-checked={o.value === value} className="seg__item" onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Shown by the shell when a tab change is attempted with unsaved edits. */
export function UnsavedDialog({ onSave, onDiscard, onStay }: { onSave: () => void; onDiscard: () => void; onStay: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => ref.current?.showModal(), []);
  return (
    <dialog ref={ref} className="udlg" aria-labelledby="udlg-title" onCancel={(e) => (e.preventDefault(), onStay())}>
      <h3 id="udlg-title" className="udlg__title">Save your changes?</h3>
      <p className="udlg__body">You changed some settings on this page. Save them, or discard them and leave.</p>
      <div className="udlg__actions">
        <button type="button" className="panel__button" onClick={onStay}>Keep editing</button>
        <button type="button" className="panel__button" onClick={onDiscard}>Discard</button>
        <button type="button" className="panel__button panel__button--primary" autoFocus onClick={onSave}>Save changes</button>
      </div>
    </dialog>
  );
}
