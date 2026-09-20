import "./Panels.css";
import { useEffect, useState } from "react";
import { getGeneralOverview, type GeneralOverview, type GeneralSettings } from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import type { EkkoStatus } from "../TopBar";
import type { HelpTopicId } from "./HelpPanel";
import { DraftInput, SettingsActions } from "./SettingsControls";

type BoolKey = { [K in keyof GeneralSettings]: GeneralSettings[K] extends boolean ? K : never }[keyof GeneralSettings];
type NumKey = { [K in keyof GeneralSettings]: GeneralSettings[K] extends number ? K : never }[keyof GeneralSettings];

const THRESHOLD_FIELDS: { key: NumKey; label: string; step: number }[] = [
  { key: "vad_threshold", label: "VAD threshold", step: 0.05 },
  { key: "wake_threshold", label: "Wake word threshold", step: 0.05 },
  { key: "command_verify_threshold", label: "Command verify threshold", step: 0.05 },
  { key: "routing_threshold", label: "Routing threshold", step: 0.01 },
];

const DURATION_FIELDS: { key: NumKey; label: string }[] = [
  { key: "min_silence_ms", label: "Min silence (ms)" },
  { key: "active_window_s", label: "Active listen window (s)" },
  { key: "feedback_tail_ms", label: "Feedback tail (ms)" },
];

const OVERRIDES: { key: BoolKey; label: string }[] = [
  { key: "no_save", label: "Disable saving audio clips" },
  { key: "no_verify", label: "Disable speaker verification" },
  { key: "no_transcribe", label: "Disable transcription" },
  { key: "no_execute", label: "Disable intent execution" },
  { key: "no_llm_fallback", label: "Disable LLM fallback" },
  { key: "no_manual_wake", label: "Disable manual wake hotkey" },
  { key: "no_hard_stop", label: "Disable hard stop hotkey" },
];

const WHISPER_MODELS: GeneralSettings["whisper_model"][] = ["tiny", "base", "small", "medium", "large-v3"];

const chord = (c: string) => c.split("+").map((k) => k[0].toUpperCase() + k.slice(1)).join("+");

function usageTiles(o: GeneralOverview) {
  const t = o.llm_total;
  return [
    { label: "Total calls", value: t.calls.toLocaleString() },
    { label: "Avg latency", value: t.avg_latency_s == null ? "—" : `${t.avg_latency_s.toFixed(1)}s` },
    { label: "Tokens (total)", value: t.tokens.toLocaleString() },
    { label: "Cached tokens", value: t.cached_tokens.toLocaleString() },
  ];
}

function lastAnswered(o: GeneralOverview, fallbackOff: boolean): { state: EkkoStatus; detail: string } {
  const { last_status, last_intent } = o.routing;
  if (last_status === "matched") return { state: "listening", detail: `Router (${last_intent})` };
  if (last_status === "missing_slot") return { state: "processing", detail: `Router (${last_intent}, missing slot)` };
  if (last_status === "no_match")
    return fallbackOff
      ? { state: "error", detail: "No match (fallback off)" }
      : { state: "processing", detail: "Gemini (fallback, NO_MATCH)" };
  return { state: "idle", detail: "No routes yet" };
}

interface ToggleSwitchProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

function ToggleSwitch({ label, checked, onChange }: ToggleSwitchProps) {
  return (
    <label className="panel__toggle">
      <input
        type="checkbox"
        className="panel__toggle-input"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="panel__toggle-track" />
      {label}
    </label>
  );
}

// Sensitive setting: read-only here, changed via code/.env per its Help topic.
function LockedField({
  label,
  value,
  topic,
  warning,
  onHelp,
}: {
  label: string;
  value: string;
  topic: HelpTopicId;
  warning?: string;
  onHelp: (topic: HelpTopicId) => void;
}) {
  return (
    <div className="panel__field">
      <label className="panel__field panel__pending" title="Locked: needs a code change first">
        <span className="panel__label">{label} (locked)</span>
        <input className="panel__input panel__mono" type="text" value={value} disabled />
      </label>
      {warning && <span className="panel__warning">{warning}</span>}
      <button type="button" className="panel__link" onClick={() => onHelp(topic)}>
        How to change →
      </button>
    </div>
  );
}

function GeneralPanel({ status, onHelp }: { status: EkkoStatus; onHelp: (topic: HelpTopicId) => void }) {
  const form = useSettingsForm<GeneralSettings>("general");
  const { saved, draft: settings } = form;
  const [overview, setOverview] = useState<GeneralOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);
  // Bumped on an unparseable entry so DraftInputs remount and drop it.
  const [rev, setRev] = useState(0);

  useEffect(() => {
    getGeneralOverview()
      .then(setOverview)
      .catch(() => setOverview(null))
      .finally(() => setOverviewLoading(false));
  }, []);

  function setNumber(key: keyof GeneralSettings, raw: string, nullable = false) {
    if (raw.trim() === "" && nullable) return form.set({ [key]: null });
    const n = Number(raw);
    if (raw.trim() === "" || Number.isNaN(n)) return setRev((r) => r + 1);
    form.set({ [key]: n });
  }

  if (form.loading || overviewLoading) {
    return (
      <div className="panel panel--wide">
        <h2>General</h2>
        <p className="panel__empty">Loading…</p>
      </div>
    );
  }

  const backendUp = overview !== null || saved !== null;
  const answered = overview && lastAnswered(overview, saved?.no_llm_fallback ?? false);

  return (
    <div className="panel panel--wide">
      <h2>General</h2>

      {overview ? (
        <div className="panel__stat-grid">
          {usageTiles(overview).map((stat) => (
            <div className="panel__stat-tile" key={stat.label}>
              <span className="panel__label">{stat.label}</span>
              <span className="panel__value panel__mono">{stat.value}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="panel__empty">Usage stats unavailable.</p>
      )}

      <div className="panel__field">
        <span className="panel__section-heading">Status</span>
        <div className="panel__card-grid">
          <div className="panel__status-card">
            <span className="panel__status-card-label">
              <span className={`status-dot status-dot--${status}`} />
              Listener
            </span>
            <span className="panel__status-card-value">{status}</span>
          </div>
          <div className="panel__status-card">
            <span className="panel__status-card-label">
              <span className={`status-dot status-dot--${backendUp ? "listening" : "error"}`} />
              Backend
            </span>
            <span className="panel__status-card-value">{backendUp ? "Connected" : "Unreachable"}</span>
          </div>
          {answered && (
            <div className="panel__status-card">
              <span className="panel__status-card-label">
                <span className={`status-dot status-dot--${answered.state}`} />
                Last answered by
              </span>
              <span className="panel__status-card-value">{answered.detail}</span>
            </div>
          )}
          {overview?.hotkeys.map((hotkey) => (
            <div className="panel__status-card" key={hotkey.name}>
              <span className="panel__status-card-label">
                <span className={`status-dot status-dot--${hotkey.enabled ? "listening" : "error"}`} />
                {hotkey.name}
              </span>
              <span className="panel__status-card-value panel__mono">{chord(hotkey.chord)}</span>
            </div>
          ))}
        </div>
      </div>

      {overview && (
        <div>
          <span className="panel__section-heading">Routing confidence (recent)</span>
          <div className="panel__bar-graph" style={{ marginTop: "var(--space-2)" }}>
            {overview.routing.recent_scores.map((value, i) => (
              <div className="panel__bar-graph-bar" key={i} style={{ height: `${value * 100}%` }} />
            ))}
          </div>
          <span className="panel__label">
            Match rate:{" "}
            {overview.routing.match_rate == null ? "—" : `${Math.round(overview.routing.match_rate * 100)}%`} ·
            Threshold: {saved?.routing_threshold ?? overview.routing.threshold}
          </span>
        </div>
      )}

      {overview && (
        <div className="panel__field">
          <span className="panel__section-heading">Memory</span>
          <span className="panel__value">
            {overview.memory.accepted} accepted · {overview.memory.rejected} rejected · {overview.memory.pending} pending
          </span>
        </div>
      )}

      <div className="panel__field">
        <span className="panel__section-heading">Configuration</span>
        <span className="panel__label">Saved to the backend. The listener still uses its CLI flags until it reads these.</span>
        {!settings ? (
          <p className="panel__empty">{form.error ?? "Settings unavailable."}</p>
        ) : (
          <div className="panel__config-grid">
            {THRESHOLD_FIELDS.map((f) => (
              <label className="panel__field" key={`${f.key}-${rev}`}>
                <span className="panel__label">{f.label}</span>
                <DraftInput
                  type="number"
                  step={f.step}
                  min={0}
                  max={1}
                  value={String(settings[f.key])}
                  onCommit={(raw) => setNumber(f.key, raw)}
                />
              </label>
            ))}

            <label className="panel__field" key={`verify_threshold-${rev}`}>
              <span className="panel__label">Speaker verify threshold</span>
              <DraftInput
                type="number"
                step={0.05}
                min={0}
                max={1}
                placeholder="auto"
                value={settings.verify_threshold == null ? "" : String(settings.verify_threshold)}
                onCommit={(raw) => setNumber("verify_threshold", raw, true)}
              />
            </label>

            <LockedField
              label="Wake word"
              value={settings.wake_word}
              topic="wake-word"
              warning="Can't be changed here. A trained wake word model has to be added in code first."
              onHelp={onHelp}
            />

            {DURATION_FIELDS.map((f) => (
              <label className="panel__field" key={`${f.key}-${rev}`}>
                <span className="panel__label">{f.label}</span>
                <DraftInput
                  type="number"
                  min={0}
                  value={String(settings[f.key])}
                  onCommit={(raw) => setNumber(f.key, raw)}
                />
              </label>
            ))}

            <label className="panel__field">
              <span className="panel__label">Whisper model</span>
              <select
                className="panel__input"
                value={settings.whisper_model}
                onChange={(e) => form.set({ whisper_model: e.target.value as GeneralSettings["whisper_model"] })}
              >
                {WHISPER_MODELS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>

            <LockedField label="EKKO_OS" value={overview?.ekko_os ?? "unknown"} topic="ekko-os" onHelp={onHelp} />
            <LockedField
              label="Gemini API key"
              topic="gemini-key"
              onHelp={onHelp}
              value={
                !overview?.gemini_key_hint
                  ? "not set"
                  : overview.gemini_key_hint === "set"
                    ? "set"
                    : `••••••••${overview.gemini_key_hint}`
              }
            />
          </div>
        )}
      </div>

      {settings && (
        <div className="panel__subsection">
          <span className="panel__section-heading">Overrides</span>
          <div className="panel__toggle-grid">
            {OVERRIDES.map((o) => (
              <ToggleSwitch key={o.key} label={o.label} checked={settings[o.key]} onChange={(v) => form.set({ [o.key]: v })} />
            ))}
          </div>
        </div>
      )}

      {settings && (
        <SettingsActions
          dirty={form.dirty}
          busy={form.busy}
          error={form.error}
          onSave={form.save}
          onReset={form.reset}
        />
      )}
    </div>
  );
}

export default GeneralPanel;
