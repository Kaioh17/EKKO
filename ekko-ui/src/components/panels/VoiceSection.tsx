import type { VoiceSettings } from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import { DraftInput, SettingsActions } from "./SettingsControls";

/** The speech-engine settings, with their own draft and save. */
function VoiceSection() {
  const voice = useSettingsForm<VoiceSettings>("voice");
  const draft = voice.draft;
  return (
    <div className="panel__subsection">
      <span className="panel__section-heading">Voice</span>
      {draft && (
        <div className="panel__config-grid">
          <label className="panel__field">
            <span className="panel__label">Speech engine</span>
            <select
              className="panel__input"
              value={draft.tts_engine}
              onChange={(e) => voice.set({ tts_engine: e.target.value as VoiceSettings["tts_engine"] })}
            >
              <option value="piper">Piper (offline, free)</option>
              <option value="openai">OpenAI (streamed, paid)</option>
              <option value="kokoro">Kokoro (offline, free, trial)</option>
              <option value="chattts">ChatTTS (offline, free, trial)</option>
            </select>
          </label>
          <label className="panel__toggle">
            <input type="checkbox" className="panel__toggle-input" checked={draft.tts_streaming} onChange={(e) => voice.set({ tts_streaming: e.target.checked })} />
            <span className="panel__toggle-track" />
            Start speaking while the answer is still generating
          </label>
          {draft.tts_engine === "kokoro" && (
            <label className="panel__field">
              <span className="panel__label">Kokoro voice</span>
              <DraftInput type="text" value={draft.kokoro_voice} onCommit={(raw) => raw.trim() && voice.set({ kokoro_voice: raw.trim() })} />
            </label>
          )}
          {draft.tts_engine === "chattts" && (
            <label className="panel__field">
              <span className="panel__label">Voice seed</span>
              <DraftInput
                type="number"
                min={0}
                step={1}
                value={String(draft.chattts_seed)}
                onCommit={(raw) => /^\d+$/.test(raw.trim()) && voice.set({ chattts_seed: Number(raw.trim()) })}
              />
            </label>
          )}
          {draft.tts_engine === "openai" && (
            <>
              <label className="panel__field">
                <span className="panel__label">OpenAI voice</span>
                <DraftInput type="text" value={draft.openai_tts_voice} onCommit={(raw) => raw.trim() && voice.set({ openai_tts_voice: raw.trim() })} />
              </label>
              <label className="panel__field">
                <span className="panel__label">OpenAI TTS model</span>
                <DraftInput type="text" value={draft.openai_tts_model} onCommit={(raw) => raw.trim() && voice.set({ openai_tts_model: raw.trim() })} />
              </label>
              <label className="panel__field">
                <span className="panel__label">Speaking style</span>
                <DraftInput type="text" value={draft.openai_tts_instructions} onCommit={(raw) => voice.set({ openai_tts_instructions: raw })} />
              </label>
            </>
          )}
        </div>
      )}
      <SettingsActions dirty={voice.dirty} busy={voice.busy} error={voice.error} onSave={voice.save} onReset={voice.reset} />
    </div>
  );
}

export default VoiceSection;
