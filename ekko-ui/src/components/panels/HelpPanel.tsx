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
      "ekko ships its own model, listener/models/hey_ekko.onnx (\"hey ekko\"), trained with openWakeWord's open source training pipeline.",
      "To change it: train another .onnx the same way, or pick a pretrained openWakeWord model (alexa, hey_mycroft, hey_jarvis, hey_rhasspy).",
      "Set DEFAULT_WAKE_WORD in listener/defaults.py to the .onnx path or the pretrained name (or pass --wake-word when starting the listener).",
      "Re-enroll your voice saying the new phrase: record 5-10 samples with voice_auth/record_sample.py, then run python voice_auth/enroll.py enrollment/ reference_embedding.pt.",
      "Re-tune verification: run the listener with --skip-wake, say the new phrase several times, and adjust the speaker verify threshold.",
      "Restart the listener and the backend. The new wake word then shows up here automatically.",
    ],
  },
  {
    id: "stt-engine",
    title: "Choosing a speech engine: Whisper or Parakeet",
    why:
      "ekko can turn your speech into text with Whisper or with Parakeet. Both run on your computer, and neither sends audio anywhere. " +
      "Whisper is the one ekko has always used, and it makes good use of a graphics card. Parakeet is NVIDIA's model, built to run on the processor. " +
      "It hasn't been timed against Whisper here yet, so Recent requests shows how each one does on your computer.",
    steps: [
      "If you're unsure, leave Engine on Automatic. With a working graphics card it uses Whisper. Without one it uses Parakeet once you've downloaded it, and Whisper until then.",
      "To try Parakeet: set Engine to Parakeet, click Download (about 660 MB, once), then click \"Restart listening to use it\".",
      "Check Running now. It names the engine and the model, and says whether it's on the graphics card or the processor.",
      "Use the English model (v2) unless you speak another European language. ekko only understands English commands, so v3 adds nothing today.",
      "To compare the two, use ekko normally with one, note Recent requests, then switch and compare. Every request is also in the segment log, listener/logs/segments.jsonl.",
      "If Parakeet can't start, ekko keeps working on Whisper and says why next to Running now.",
    ],
  },
  {
    id: "stt-gpu",
    title: "Why my graphics card isn't used",
    why:
      "Speech recognition on an NVIDIA graphics card needs NVIDIA's CUDA libraries (cuBLAS and cuDNN). They add about 1.4 GB, so the installed ekko doesn't include them. " +
      "When ekko can't load them it uses the processor instead. Nothing is broken, and ekko keeps working.",
    steps: [
      "The easy answer is to do nothing. On the processor, Automatic uses Parakeet once it's downloaded, and Whisper's small model until then.",
      "If you run ekko from source and want the graphics card: install the CUDA build of PyTorch from pytorch.org over the CPU one (see the GPU section of the readme). Whisper picks up the libraries that come with it. Restart the listener, then check that Running now says GPU.",
      "To skip the graphics card on purpose, set Processor to CPU.",
      "If Running now still says processor after that, the listener log (backend/logs/listener.log) has the exact error.",
    ],
  },
  {
    id: "stt-calibrate",
    title: "What Calibrate does",
    why:
      "Using more processor threads isn't always faster, especially on laptops that mix fast and efficient cores. Calibrate measures it instead of guessing.",
    steps: [
      "It transcribes a real clip of yours (or three seconds of noise if you have none saved) with the Whisper model at several thread counts: 2, 4, 6, 8, 12, 16, half your cores and all of them, where your computer has that many.",
      "It loads the model once for each count, so it takes a while. Leave the page open.",
      "It picks the fewest threads that are within 5% of the fastest, because the microphone, voice detection and speech need cores too. The number appears in Processor threads. Save to keep it.",
      "It times Whisper, not Parakeet. Parakeet uses the same setting, so treat the result as a sensible starting point for it, not a measurement.",
      "Leave the box at 0 to let ekko use half your cores.",
    ],
  },
  {
    id: "hold-to-talk",
    title: "Hold to talk",
    why:
      "Hold a key, speak, let go. There's no wake word, no spoken greeting, and no waiting for ekko to be sure you've finished. It's optional and off by default.",
    steps: [
      "Switch on Hold to talk at the top of General & Voice, pick a different key if you want one, and save. Saving restarts the listener.",
      "Hold the key, say your request, and let go. ekko starts working the moment you let go.",
      "To choose your own keys, click the key and press the combination. It needs Ctrl, Alt or Windows (or must be a function key on its own), and it can't be Ctrl+Alt+W or Ctrl+Alt+Q.",
      "If nothing happens, check that it's switched on and saved, and that no other program uses the same keys. The listener log says \"not usable\" if the key can't be registered.",
      "A hold ends on its own after 30 seconds. Ctrl+Alt+Q cancels it.",
      "The key isn't blocked, so the app you're in also sees it. Pick a combination your apps don't use.",
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
      <p className="panel__empty">
        Longer explanations. The first guide covers a setting that needs a code change. The others explain choices you can make in the app.
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
