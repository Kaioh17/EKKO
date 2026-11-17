import "./ProgressSteps.css";
import { Check, CircleAlert, Loader, Minus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { ProgressStep } from "../api/client";

// A progress step plus when this client first saw it, so a running one can show a live clock.
export type LiveStep = ProgressStep & { startedAt: number };

// Add a step, or update the one with the same id in place (rows keep their order).
export function upsertStep(steps: LiveStep[], step: ProgressStep): LiveStep[] {
  const at = steps.findIndex((s) => s.id === step.id);
  if (at < 0) return [...steps, { ...step, startedAt: Date.now() }];
  return steps.map((s, i) => (i === at ? { ...step, startedAt: s.startedAt } : s));
}

// Date.now(), refreshed every 100 ms while `active`. One shared tick instead of a timer per row.
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

// The one place a screen keeps its step list: push each incoming step, reset between turns.
// `now` ticks while any step is running, or while `tickAlso` is true (chat's total wait counter).
export function useSteps(tickAlso = false) {
  const [steps, setSteps] = useState<LiveStep[]>([]);
  const now = useNow(tickAlso || steps.some((s) => s.state === "running"));
  const push = useCallback((step: ProgressStep) => setSteps((prev) => upsertStep(prev, step)), []);
  const reset = useCallback(() => setSteps([]), []);
  return { steps, now, push, reset };
}

export const formatMs = (ms: number) => (ms < 1000 ? `${Math.max(0, Math.round(ms))}ms` : `${(ms / 1000).toFixed(1)}s`);

const ICONS = {
  running: <Loader size={14} className="steps__spin" />,
  done: <Check size={14} />,
  warn: <CircleAlert size={14} />,
  failed: <X size={14} />,
  skipped: <Minus size={14} />,
};

// "Working on it" plus one row per step: [icon] label  detail  time. Nothing here knows
// what the steps are about; they come from routing/progress.py.
export function ProgressSteps({ steps, now, title = "Working on it" }: { steps: LiveStep[]; now: number; title?: string }) {
  if (!steps.length) return null;
  return (
    <div className="steps" role="status" aria-live="polite">
      <div className="steps__title">{title}</div>
      <ul className="steps__list">
        {steps.map((s) => (
          <li key={s.id} className={`steps__row steps__row--${s.state}`} aria-label={`${s.label}: ${s.state}${s.detail ? `, ${s.detail}` : ""}`}>
            <span className="steps__icon" aria-hidden="true">{ICONS[s.state]}</span>
            <span className="steps__label">{s.label}</span>
            {s.detail && <span className="steps__detail">{s.detail}</span>}
            <span className="steps__time">{s.state === "skipped" ? "" : formatMs(s.ms ?? now - s.startedAt)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
