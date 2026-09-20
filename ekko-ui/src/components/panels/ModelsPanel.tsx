import "./Panels.css";

// The three llm_fallback/ backends, mirroring their READMEs -- `active`
// tracks which one listener/vad_listener.py actually imports and calls
// (llm_fallback/gemini/fallback_gemini.py's attempt_fallback(), per
// llm_fallback/README.md's "Integration" section). No live toggle exists
// today; this is a hardcoded fact about the current wiring, not runtime
// state, so it's listed here rather than fetched.
const FALLBACK_MODELS = [
  {
    name: "Gemini",
    detail: "Free-tier API — live NO_MATCH fallback",
    active: true,
  },
  {
    name: "Claude (Haiku)",
    detail: "claude -p subprocess — reference/rollback path, not called",
    active: false,
  },
  {
    name: "Ollama (qwen2.5:7b)",
    detail: "Local GPU prototype — not wired up",
    active: false,
  },
] as const;

function ModelsPanel() {
  return (
    <div className="panel">
      <h2>Models</h2>
      <ul className="panel__list">
        {FALLBACK_MODELS.map((model) => (
          <li
            key={model.name}
            className="panel__list-item"
            style={model.active ? { borderColor: "#34c759", background: "rgba(52, 199, 89, 0.1)" } : undefined}
          >
            <span className="panel__mono">
              {model.name}
              {model.active && " (selected)"}
            </span>
            <span>{model.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default ModelsPanel;
