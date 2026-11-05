import "./ChatPanel.css";
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  clearTodaySummary,
  getTodaySummary,
  getWindow,
  refreshTodaySummary,
  type ConversationWindow,
  type TodaySummary,
} from "../../api/client";

// What the next LLM call sees beyond long-term facts: today's summary of what
// scrolled out, then the live conversation window, whole.
function ActiveMemory() {
  const [win, setWin] = useState<ConversationWindow | null>(null);
  const [summary, setSummary] = useState<TodaySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    Promise.all([getWindow(), getTodaySummary()])
      .then(([w, s]) => {
        setWin(w);
        setSummary(s);
      })
      .catch(() => setWin(null))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
    refresh();
  }

  if (loading) return <p className="panel__empty">Loading…</p>;
  if (!win) return <p className="panel__empty">Could not load the live conversation.</p>;

  const pct = Math.min(100, Math.round((win.tokens_est / win.budget) * 100));

  return (
    <>
      <div className="panel__stat-grid">
        <div className="panel__stat-tile">
          <span className="panel__label">Live conversation</span>
          <span className="panel__value">
            {win.turns.length} exchange{win.turns.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="panel__stat-tile">
          <span className="panel__label">Size</span>
          <span className="panel__value">
            ~{win.tokens_est.toLocaleString()} / {win.budget.toLocaleString()} tokens ({pct}%)
          </span>
        </div>
      </div>

      <h3 className="panel__section-heading">Today so far</h3>
      {summary ? (
        <div className="panel__stack">
          <p>{summary.summary}</p>
          {summary.topics.length > 0 && <span className="panel__label">{summary.topics.join(" · ")}</span>}
          <span className="panel__mono panel__label">
            covers up to {summary.covered_until.slice(11, 16)} · updated {summary.updated_at.slice(11, 16)}
            {summary.pending_turns > 0 && ` · ${summary.pending_turns} newer exchange(s) not folded in yet`}
          </span>
        </div>
      ) : (
        <p className="panel__empty">
          {win.evicted_turns > 0
            ? "Older exchanges from today will be summarized here within a few minutes."
            : "Everything from today still fits in the live conversation, so there's nothing to summarize yet."}
        </p>
      )}
      <div className="panel__actions">
        <button type="button" className="panel__button" disabled={busy} onClick={() => run(refreshTodaySummary)}>
          {busy ? "Summarizing…" : "Update summary now"}
        </button>
        {summary && (
          <button
            type="button"
            className="panel__button panel__button--danger"
            disabled={busy}
            onClick={() => run(clearTodaySummary)}
          >
            Clear summary
          </button>
        )}
      </div>
      {error && <p className="panel__warning">{error}</p>}

      <h3 className="panel__section-heading">Live conversation (what the AI sees, whole)</h3>
      {win.turns.length === 0 ? (
        <p className="panel__empty">No conversation in the last 24 hours.</p>
      ) : (
        <ul className="panel__list">
          {[...win.turns].reverse().map((t) => (
            <li className="panel__list-item" key={t.ts}>
              <div className="panel__stack">
                <strong>{t.user}</strong>
                <div className="chat-md">
                  <Markdown remarkPlugins={[remarkGfm]}>{t.assistant || "_(no reply)_"}</Markdown>
                </div>
                <span className="panel__mono panel__label">
                  {t.ts.slice(0, 16).replace("T", " ")} · {t.source} · {t.kind}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default ActiveMemory;
