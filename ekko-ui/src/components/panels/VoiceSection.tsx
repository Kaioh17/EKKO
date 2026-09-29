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
            </select>
          </label>
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
