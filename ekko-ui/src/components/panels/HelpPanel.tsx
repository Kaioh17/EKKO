import "./Panels.css";
import { useEffect } from "react";

// Settings that need a code change before they can move. Other panels
// link here via HelpLink with a topic id; the backend's locked fields
// (backend/schemas/settings.py `locked(topic)`) use the same ids.
export const HELP_TOPICS = [
  {
    id: "wake-word",
    title: "Changing the wake word",
    why:
      "The wake word is a trained openWakeWord model, not a free-text phrase, and speaker verification is tuned " +
      "against you saying the current one. Changing the name alone would break detection and verification.",
    steps: [
      "Pick a pretrained openWakeWord model (alexa, hey_mycroft, hey_jarvis, hey_rhasspy) or train a custom one to an .onnx file.",
      "Custom model only: update _build_wake_word_model() in listener/vad_listener.py to load your .onnx path; it only resolves pretrained names today.",
      "Set DEFAULT_WAKE_WORD in listener/vad_listener.py (or pass --wake-word when starting the listener).",
      "Re-enroll your voice saying the new phrase: record 5-10 samples with voice_auth/record_sample.py, then run python voice_auth/enroll.py enrollment/ reference_embedding.pt.",
      "Re-tune verification: run the listener with --skip-wake, say the new phrase several times, and adjust the speaker verify threshold.",
      "Restart the listener and the backend. The new wake word then shows up here automatically.",
    ],
  },
  {
    id: "ekko-os",
    title: "Changing EKKO_OS",
    why: "It picks which scripts/<os>/ handler tree every intent runs. The wrong value makes every command fail.",
    steps: [
      "Set EKKO_OS in the project's .env to windows, linux, mac, or auto (see .env.example).",
      "mac has no scripts/mac/ tree yet, so create one before choosing it.",
      "Restart the backend and the listener.",
    ],
  },
  {
    id: "gemini-key",
    title: "Changing the Gemini API key",
    why: "It's a secret, so it lives only in .env. The UI never stores it or shows it in full.",
    steps: [
      "Get a key at https://aistudio.google.com/apikey (the free tier works).",
      "Set GEMINI_API_KEY in the project's .env.",
      "Restart the backend and the listener.",
    ],
  },
] as const;

export type HelpTopicId = (typeof HELP_TOPICS)[number]["id"];

function HelpPanel({ topic }: { topic?: HelpTopicId }) {
  useEffect(() => {
    if (topic) document.getElementById(`help-${topic}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [topic]);

  return (
    <div className="panel panel--wide">
      <h2>Help</h2>
      <p className="panel__empty">
        These settings can't be changed from the UI. Each one needs a code or .env change first.
      </p>
      {HELP_TOPICS.map((t) => (
        <section
          key={t.id}
          id={`help-${t.id}`}
          className={`panel__subsection${t.id === topic ? " panel__subsection--active" : ""}`}
        >
          <span className="panel__section-heading">{t.title}</span>
          <span className="panel__value">{t.why}</span>
          <ol className="panel__steps">
            {t.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

export default HelpPanel;
