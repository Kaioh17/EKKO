import { useEffect, useState } from "react";
import TurnRow from "./TurnRow";
import { clearDayLog, deleteDayTurn, getDayLog, type DayLog } from "../../api/client";

function TodayMemory() {
  const [log, setLog] = useState<DayLog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    getDayLog()
      .then(setLog)
      .catch(() => setLog(null))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  async function run(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  }

  if (loading) return <p className="panel__empty">Loading…</p>;
  if (!log) return <p className="panel__empty">Could not load the last 24 hours.</p>;

  return (
    <>
      <div className="panel__stat-grid">
        <div className="panel__stat-tile">
          <span className="panel__label">Exchanges in the last {log.window_hours}h</span>
          <span className="panel__value">{log.turns.length}</span>
        </div>
        <div className="panel__stat-tile">
          <span className="panel__label">In the live conversation</span>
          <span className="panel__value">
            ~{log.tokens_est.toLocaleString()} / {log.token_budget.toLocaleString()} tokens
          </span>
        </div>
      </div>
      {log.truncated_turns > 0 && (
        <p className="panel__warning">
          {log.truncated_turns} older exchange{log.truncated_turns === 1 ? " has" : "s have"} scrolled out of the live
          conversation. The AI sees {log.truncated_turns === 1 ? "it" : "them"} through today's summary (Now tab).
        </p>
      )}
      {error && <p className="panel__warning">{error}</p>}
      {log.turns.length === 0 ? (
        <p className="panel__empty">Nothing in the last 24 hours.</p>
      ) : (
        <>
          <ul className="panel__list">
            {[...log.turns].reverse().map((t) => (
              <TurnRow key={t.ts} turn={t} onDelete={() => run(() => deleteDayTurn(t.ts))}>
                <span className="panel__label">{t.assistant || "(no reply)"}</span>
              </TurnRow>
            ))}
          </ul>
          <button
            type="button"
            className="panel__button panel__button--danger"
            onClick={() => {
              if (window.confirm("Delete everything EKKO has saved from the last 24 hours?")) void run(clearDayLog);
            }}
          >
            Delete all
          </button>
        </>
      )}
    </>
  );
}

export default TodayMemory;
