import "./Panels.css";
import { useEffect, useState } from "react";
import { getContext, type AiContext } from "../../api/client";

// What ekko's next reply is given besides its fixed instructions. Re-read each
// time it opens, so it matches the latest messages.
function ContextView({ onClose }: { onClose: () => void }) {
  const [ctx, setCtx] = useState<AiContext | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getContext().then(setCtx).catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  return (
    <div className="context-backdrop" onClick={onClose}>
    <section className="guide context-view" role="dialog" aria-label="Current context" onClick={(e) => e.stopPropagation()}>
      <div className="panel__actions">
        <h3 className="panel__section-heading">
          What ekko sees with your next message
          {ctx && ` · ~${ctx.total_tokens.toLocaleString()} tokens`}
        </h3>
        <button type="button" className="panel__link" onClick={onClose}>
          Close
        </button>
      </div>
      {failed && <p className="panel__warning">Could not load the context.</p>}
      {!ctx && !failed && <p className="panel__empty">Loading…</p>}
      {ctx?.sections.map((s) => (
        <details key={s.name} className="context-view__section">
          <summary>
            <strong>{s.name}</strong>
            <span className="panel__mono panel__label">{s.text ? `~${s.tokens.toLocaleString()} tokens` : "empty"}</span>
          </summary>
          {s.text && <pre>{s.text}</pre>}
        </details>
      ))}
      <p className="panel__label">
        Plus ekko's fixed instructions and command list, which are the same every time. Manage this memory on the Memory
        page.
      </p>
    </section>
    </div>
  );
}

export default ContextView;
