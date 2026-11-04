import "./Panels.css";
import "./Dashboard.css";
import "./BriefingPanel.css";
import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Info, Search, X } from "lucide-react";
import type { GeneralSettings } from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import type { HelpTopicId } from "./HelpPanel";
import { DraftInput, LockedField, SettingsActions, ToggleSwitch } from "./SettingsControls";
import VoiceSection from "./VoiceSection";

type NumKey = { [K in keyof GeneralSettings]: GeneralSettings[K] extends number ? K : never }[keyof GeneralSettings];
type BoolKey = { [K in keyof GeneralSettings]: GeneralSettings[K] extends boolean ? K : never }[keyof GeneralSettings];
type Group = "processing" | "voice";

const THRESHOLDS: { key: NumKey; label: string; step: number }[] = [
  { key: "vad_threshold", label: "VAD threshold", step: 0.05 },
  { key: "wake_threshold", label: "Wake word threshold", step: 0.05 },
  { key: "command_verify_threshold", label: "Command verify threshold", step: 0.05 },
];

const DURATIONS: { key: NumKey; label: string }[] = [
  { key: "min_silence_ms", label: "Min silence, wake (ms)" },
  { key: "command_min_silence_ms", label: "Min silence, active listen (ms)" },
  { key: "active_window_s", label: "Active listen window (s)" },
  { key: "feedback_tail_ms", label: "Feedback tail (ms)" },
];

const OVERRIDES: { key: BoolKey; label: string }[] = [
  { key: "no_save", label: "Disable saving audio clips" },
  { key: "no_verify", label: "Disable speaker verification" },
  { key: "no_transcribe", label: "Disable transcription" },
];

const WHISPER_MODELS: GeneralSettings["whisper_model"][] = ["auto", "tiny", "base", "small", "medium", "large-v3"];

// Drives both the info popover and the search (label + description are matched).
const INFO: { label: string; group: Group; desc: string }[] = [
  { label: "Wake word", group: "processing", desc: "The phrase that starts ekko listening. Locked: a trained wake word model has to be added in code first." },
  { label: "Microphone", group: "processing", desc: "Which input device ekko listens on. Not available yet; it uses the system default." },
  { label: "VAD threshold", group: "processing", desc: "How sure the voice detector must be that a sound is speech (0-1). Higher ignores more background noise but can clip quiet speech." },
  { label: "Wake word threshold", group: "processing", desc: "How closely audio must match the wake word to wake ekko (0-1). Higher means fewer false wakes but is harder to trigger." },
  { label: "Command verify threshold", group: "processing", desc: "Confidence needed to accept a follow-up command as the enrolled speaker while ekko is actively listening (0-1)." },
  { label: "Speaker verify threshold", group: "processing", desc: "How closely a voice must match the enrolled speaker to be accepted after the wake word (0-1). Blank picks a value automatically." },
  { label: "Min silence, wake (ms)", group: "processing", desc: "Silence that ends an utterance while ekko waits for the wake word. Shorter reacts faster; longer tolerates pauses." },
  { label: "Min silence, active listen (ms)", group: "processing", desc: "Silence that ends your command once ekko is listening. Raise it if ekko cuts you off mid-sentence." },
  { label: "Active listen window (s)", group: "processing", desc: "How long ekko keeps listening for a command after it wakes, before going back to idle." },
  { label: "Feedback tail (ms)", group: "processing", desc: "Extra wait after ekko finishes speaking before it listens again, so it doesn't hear its own voice." },
  { label: "Transcription model", group: "processing", desc: "The Whisper model that turns speech into text. Bigger models are more accurate but slower; auto picks one for your machine." },
  { label: "Disable saving audio clips", group: "processing", desc: "Stops ekko keeping recordings of what it hears on disk." },
  { label: "Disable speaker verification", group: "processing", desc: "Skips the check that the voice belongs to you, so anyone can give commands." },
  { label: "Disable transcription", group: "processing", desc: "Turns off speech-to-text entirely. ekko will hear you but won't understand commands." },
  { label: "Speech engine", group: "voice", desc: "What generates ekko's voice: Piper, Kokoro and ChatTTS run offline for free; OpenAI is streamed and paid." },
  { label: "Start speaking while generating", group: "voice", desc: "ekko begins talking as soon as the first words arrive instead of waiting for the full answer. Faster, but can sound choppier." },
  { label: "Voice options", group: "voice", desc: "Engine-specific settings: Kokoro voice, ChatTTS voice seed, or OpenAI voice, model and speaking style." },
];

function AudioPanel({ onHelp }: { onHelp: (topic: HelpTopicId) => void }) {
  const form = useSettingsForm<GeneralSettings>("general");
  const s = form.draft;
  // Bumped on an unparseable entry so DraftInputs remount and drop it.
  const [rev, setRev] = useState(0);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Record<Group, boolean>>({ processing: true, voice: true });
  const [showInfo, setShowInfo] = useState(false);

  const q = query.trim().toLowerCase();
  const hit = (label: string) => {
    const i = INFO.find((x) => x.label === label);
    return !q || label.toLowerCase().includes(q) || (i?.desc.toLowerCase().includes(q) ?? false);
  };
  const groupHit = (g: Group) => INFO.some((i) => i.group === g && hit(i.label));

  const setNum = (key: NumKey | "verify_threshold", raw: string, nullable = false) => {
    if (raw.trim() === "" && nullable) return form.set({ [key]: null });
    const n = Number(raw);
    if (raw.trim() === "" || Number.isNaN(n)) return setRev((r) => r + 1);
    form.set({ [key]: n });
  };

  // A search forces matching sections open so results are never hidden.
  const section = (group: Group, title: string, children: ReactNode) =>
    groupHit(group) && (
      <>
        <button type="button" className="brief__bar audio__toggle" onClick={() => setOpen({ ...open, [group]: !open[group] })}>
          <span className="brief__label">{title}</span>
          {q || open[group] ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        {(q || open[group]) && <div className="audio__body">{children}</div>}
      </>
    );

  return (
    <div className="brief">
      <div className="dash__head audio__head">
        <div>
          <p>How ekko hears you, and how it sounds.</p>
        </div>
        <label className="audio__search">
          <Search size={15} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search settings…" />
          {query && (
            <button type="button" aria-label="Clear search" onClick={() => setQuery("")}>
              <X size={14} />
            </button>
          )}
        </label>
      </div>

      {q && !groupHit("processing") && !groupHit("voice") && (
        <p className="panel__empty audio__body">No settings match “{query}”.</p>
      )}

      {section(
        "processing",
        "Audio processing",
        !s ? (
          <p className="panel__empty">{form.loading ? "Loading…" : (form.error ?? "Settings unavailable.")}</p>
        ) : (
          <>
            <div className="panel__config-grid">
              {hit("Wake word") && (
                <LockedField
                  label="Wake word"
                  value={s.wake_word}
                  topic="wake-word"
                  warning="Can't be changed here. A trained wake word model has to be added in code first."
                  onHelp={onHelp}
                />
              )}
              {/* No backend support for choosing an input device: shown grayed out. */}
              {hit("Microphone") && (
                <label className="panel__field is-off" title="Not available yet">
                  <span className="panel__label">Microphone</span>
                  <select className="panel__input" tabIndex={-1}>
                    <option>System default</option>
                  </select>
                </label>
              )}
              {THRESHOLDS.filter((f) => hit(f.label)).map((f) => (
                <label className="panel__field" key={`${f.key}-${rev}`}>
                  <span className="panel__label">{f.label}</span>
                  <DraftInput type="number" step={f.step} min={0} max={1} value={String(s[f.key])} onCommit={(raw) => setNum(f.key, raw)} />
                </label>
              ))}
              {hit("Speaker verify threshold") && (
                <label className="panel__field" key={`verify-${rev}`}>
                  <span className="panel__label">Speaker verify threshold</span>
                  <DraftInput
                    type="number"
                    step={0.05}
                    min={0}
                    max={1}
                    placeholder="auto"
                    value={s.verify_threshold == null ? "" : String(s.verify_threshold)}
                    onCommit={(raw) => setNum("verify_threshold", raw, true)}
                  />
                </label>
              )}
              {DURATIONS.filter((f) => hit(f.label)).map((f) => (
                <label className="panel__field" key={`${f.key}-${rev}`}>
                  <span className="panel__label">{f.label}</span>
                  <DraftInput type="number" min={0} value={String(s[f.key])} onCommit={(raw) => setNum(f.key, raw)} />
                </label>
              ))}
              {hit("Transcription model") && (
                <label className="panel__field">
                  <span className="panel__label">Transcription model</span>
                  <select
                    className="panel__input"
                    value={s.whisper_model}
                    onChange={(e) => form.set({ whisper_model: e.target.value as GeneralSettings["whisper_model"] })}
                  >
                    {WHISPER_MODELS.map((m) => (
                      <option key={m} value={m}>
                        {m === "auto" ? "Whisper · auto (recommended)" : `Whisper ${m}`}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <div className="panel__toggle-grid">
              {OVERRIDES.filter((o) => hit(o.label)).map((o) => (
                <ToggleSwitch key={o.key} label={o.label} checked={s[o.key]} onChange={(v) => form.set({ [o.key]: v })} />
              ))}
            </div>
            <span className="panel__label">Saving restarts the voice listener so the changes take effect.</span>
            <SettingsActions dirty={form.dirty} busy={form.busy} error={form.error} onSave={form.save} onReset={form.reset} />
          </>
        ),
      )}

      {section("voice", "ekko's voice", <VoiceSection />)}

      <button type="button" className="audio__info-btn" aria-label="What each setting does" onClick={() => setShowInfo(!showInfo)}>
        {showInfo ? <X size={18} /> : <Info size={18} />}
      </button>
      {showInfo && (
        <div className="audio__info">
          <b>What each setting does</b>
          {INFO.filter((i) => hit(i.label)).map((i) => (
            <div key={i.label}>
              <span>{i.label}</span>
              <p>{i.desc}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default AudioPanel;
