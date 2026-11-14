import "./Panels.css";
import { useEffect, useState } from "react";
import {
  calibrateStt,
  downloadSttModel,
  getAudio,
  getSttHardware,
  getSttModels,
  getSttStatus,
  restartListener,
  type AudioState,
  type GeneralSettings,
  type SttCalibration,
  type SttEngineInfo,
  type SttHardware,
  type SttModel,
  type SttRecent,
  type VoiceSettings,
} from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import type { HelpTopicId } from "./HelpPanel";
import { DraftInput } from "./SettingsControls";
import { CAN_DO, useAutostartForm } from "./GeneralPanel";
import { HotkeyInput, KeyCaps } from "./HotkeyInput";
import { Row, Segmented, SettingsPage, SettingsSection, Switch } from "./SettingsKit";

// Plain-language choices mapped onto the raw thresholds/timings.
const WAKE_SENSITIVITY = [
  { label: "Strict", value: 0.7 },
  { label: "Balanced", value: 0.5 },
  { label: "Eager", value: 0.3 },
];
const REPLY_WAIT = [
  { label: "Quick", value: 500 },
  { label: "Normal", value: 800 },
  { label: "Patient", value: 1500 },
];
const FOLLOW_UP = [
  { label: "5 s", value: 5 },
  { label: "10 s", value: 10 },
  { label: "20 s", value: 20 },
];

const WHISPER_MODELS: { value: GeneralSettings["whisper_model"]; label: string }[] = [
  { value: "auto", label: "Automatic · medium on a GPU, small otherwise" },
  { value: "tiny", label: "Tiny · fastest, least accurate" },
  { value: "base", label: "Base · fast" },
  { value: "small", label: "Small · good on a laptop CPU" },
  { value: "medium", label: "Medium · accurate, wants a GPU" },
  { value: "large-v3", label: "Large · most accurate, slowest" },
];
const ACK_STYLES: { label: string; value: GeneralSettings["ack_style"] }[] = [
  { label: "Say hi", value: "speech" },
  { label: "Chime", value: "chime" },
  { label: "Nothing", value: "none" },
];
const MUSIC_PLATFORMS: { label: string; value: GeneralSettings["preferred_music_platform"] }[] = [
  { label: "Automatic", value: "auto" },
  { label: "Spotify", value: "spotify" },
  { label: "Apple Music", value: "apple_music" },
];
const seconds = (ms: number) => `${(ms / 1000).toFixed(ms < 950 ? 2 : 1)} s`;

/** One plain sentence on how the last requests went. */
function describeRecent(r: SttRecent | null): string {
  if (!r) return "Unavailable.";
  if (r.n_segments === 0) return "No requests yet. After you talk to ekko, how fast and how reliable it was shows up here.";
  const ignored = Object.values(r.rejected).reduce((a, b) => a + b, 0);
  const why = Object.entries(r.rejected)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([reason, n]) => `${n} ${reason}`)
    .join(", ");
  const speed = r.stt_p50_ms == null ? "" : ` Turning speech into text took about ${seconds(r.stt_p50_ms)} (slowest tenth: ${seconds(r.stt_p90_ms ?? r.stt_p50_ms)}).`;
  const glitches = r.overflows > 0 ? ` The microphone dropped audio ${r.overflows} ${r.overflows === 1 ? "time" : "times"}.` : "";
  return `Last ${r.n_segments}: ${r.accepted} understood${ignored ? `, ${ignored} ignored (${why})` : ""}.${speed}${glitches}`;
}

/** A GPU that exists but can't be used is the commonest surprise, so say it plainly. */
function explainFallback(reason: string, engine: SttEngineInfo["engine"]): string {
  if (!/CUDA unavailable/i.test(reason)) return reason;
  return `Your graphics card can’t be used: ekko doesn’t include NVIDIA’s CUDA libraries (over 1 GB), so it runs on the processor. ${
    engine === "parakeet" ? "Parakeet is running there instead." : "Parakeet, below, is built for running on the processor."
  }`;
}

const DEFAULT_HOLD_KEY = "ctrl+alt+space";
const ENGINE_CHOICES: { label: string; value: GeneralSettings["stt_engine"] }[] = [
  { label: "Automatic", value: "auto" },
  { label: "Whisper", value: "whisper" },
  { label: "Parakeet", value: "parakeet" },
];
const ENGINE_NAMES = { whisper: "Whisper", parakeet: "Parakeet" };
const PROCESSORS: { label: string; value: GeneralSettings["whisper_device"] }[] = [
  { label: "Auto", value: "auto" },
  { label: "GPU", value: "gpu" },
  { label: "CPU", value: "cpu" },
];

// Rarely needed. Limits mirror backend/schemas/settings.py, which rejects anything else on save.
type NumKey =
  | "min_silence_ms"
  | "command_min_silence_ms"
  | "vad_threshold"
  | "feedback_tail_ms"
  | "preroll_ms"
  | "word_correction_threshold"
  | "min_command_ms"
  | "command_verify_threshold"
  | "max_no_speech_prob"
  | "min_avg_logprob";
const ADVANCED: { key: NumKey; title: string; hint: string; min: number; max: number }[] = [
  { key: "command_min_silence_ms", title: "Pause that ends a request (ms)", hint: "Silence after your request before ekko starts working. Shorter feels faster but can cut you off mid-sentence.", min: 0, max: 10000 },
  { key: "min_silence_ms", title: "Pause that ends the wake word (ms)", hint: "Same, while waiting for “Hey ekko”. Keep it short so waking feels instant.", min: 0, max: 10000 },
  { key: "vad_threshold", title: "Voice detection (0–1)", hint: "How sure ekko must be that it’s hearing speech. Raise it in a noisy room.", min: 0, max: 1 },
  { key: "feedback_tail_ms", title: "Quiet time after ekko speaks (ms)", hint: "ekko ignores the mic for this long after talking so it doesn’t hear its own voice.", min: 0, max: 5000 },
  { key: "preroll_ms", title: "Audio kept from before you speak (ms)", hint: "ekko only notices speech a moment after it starts; this keeps the lead-in so the first word isn’t clipped. Set 0 to turn off.", min: 0, max: 2000 },
  { key: "word_correction_threshold", title: "Fix near-miss spellings (0–1)", hint: "How loosely ekko matches what it heard to your custom words. 0 turns it off; higher risks changing real words.", min: 0, max: 1 },
  { key: "min_command_ms", title: "Shortest request (ms)", hint: "Anything briefer is treated as a click or a bump, not a request.", min: 0, max: 5000 },
  { key: "command_verify_threshold", title: "Voice match for requests (0–1)", hint: "How closely a request must match your enrolled voice.", min: 0, max: 1 },
  { key: "max_no_speech_prob", title: "Reject if it may not be speech (0–1)", hint: "Lower rejects more doubtful clips. Raise it if real requests are being ignored.", min: 0, max: 1 },
  { key: "min_avg_logprob", title: "Reject unsure transcripts (0 or below)", hint: "Closer to 0 rejects more. Lower it (for example -1.2) if real requests are being ignored.", min: -10, max: 0 },
];

const ENGINES: { value: VoiceSettings["tts_engine"]; label: string }[] = [
  { value: "piper", label: "Piper · offline, free" },
  { value: "kokoro", label: "Kokoro · offline, free (trial)" },
  { value: "chattts", label: "ChatTTS · offline, free (trial)" },
  { value: "openai", label: "OpenAI · online, paid" },
];

// Matches a Segmented option; a hand-edited value outside the presets selects none.
const pick = <T extends number>(opts: { value: T }[], v: number) => opts.find((o) => o.value === v)?.value as T;

function HelpLink({ topic, onHelp, children }: { topic: HelpTopicId; onHelp: (t: HelpTopicId) => void; children: string }) {
  return (
    <button type="button" className="panel__link" onClick={() => onHelp(topic)}>
      {children} →
    </button>
  );
}

function AudioPanel({ part, onHelp }: { part: "general" | "audio"; onHelp: (topic: HelpTopicId) => void }) {
  const general = part === "general";
  const form = useSettingsForm<GeneralSettings>("general");
  const voice = useSettingsForm<VoiceSettings>("voice");
  const autostart = useAutostartForm();
  const s = form.draft;
  const v = voice.draft;
  const [audio, setAudio] = useState<AudioState | null>(null);
  const [hw, setHw] = useState<SttHardware | null>(null);
  const [calibration, setCalibration] = useState<SttCalibration | null>(null);
  const [calibrating, setCalibrating] = useState(false);
  const [calibrateError, setCalibrateError] = useState<string | null>(null);

  const [models, setModels] = useState<SttModel[]>([]);
  const [running, setRunning] = useState<SttEngineInfo | null>(null);
  const [recent, setRecent] = useState<SttRecent | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    getSttHardware().then(setHw).catch(() => setHw(null));
  }, []);

  const refreshModels = () => getSttModels().then(setModels).catch(() => undefined);
  useEffect(() => {
    void refreshModels();
    const refresh = () =>
      getSttStatus()
        .then((r) => {
          setRunning(r.engine);
          setRecent(r.recent);
        })
        .catch(() => setRunning(null));
    void refresh();
    const id = setInterval(refresh, 5000); // the listener takes a few seconds to report after a restart
    return () => clearInterval(id);
  }, []);
  // A download takes minutes: follow it until it stops.
  useEffect(() => {
    if (!models.some((m) => m.state === "running")) return;
    const id = setInterval(() => void refreshModels(), 1500);
    return () => clearInterval(id);
  }, [models]);

  useEffect(() => {
    const refresh = () => getAudio().then(setAudio).catch(() => setAudio(null));
    refresh();
    // Plugging a headset in shows up without reopening the page.
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  if (form.loading || voice.loading) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">Loading…</p>
      </div>
    );
  }
  if (!s || !v) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">{form.error ?? voice.error ?? "Settings unavailable."}</p>
      </div>
    );
  }

  // A saved device that's unplugged stays listed, marked, so it isn't silently dropped.
  const deviceSelect = (key: "input_device" | "output_device", kind: "input" | "output", fallback?: string | null) => (id: string) => {
    const current = s[key];
    const names = audio?.[kind].map((d) => d.name) ?? [];
    return (
      <select
        className="panel__input"
        aria-labelledby={id}
        value={current ?? ""}
        disabled={!audio?.available}
        onChange={(e) => form.set({ [key]: e.target.value || null })}
      >
        <option value="">{`System default${fallback ? ` (${fallback})` : ""}`}</option>
        {current && !names.includes(current) && <option value={current}>{current} (not connected)</option>}
        {names.map((n) => (
          <option key={n} value={n}>{n}</option>
        ))}
      </select>
    );
  };

  const startDownload = (id: string) => {
    setDownloadError(null);
    downloadSttModel(id)
      .then(() => refreshModels())
      .catch((e: Error) => setDownloadError(e.message));
  };

  const useNow = () => {
    setRestarting(true);
    restartListener()
      .catch((e: Error) => setDownloadError(e.message))
      .finally(() => setTimeout(() => setRestarting(false), 8000));
  };

  const calibrate = () => {
    setCalibrating(true);
    setCalibrateError(null);
    calibrateStt()
      .then((r) => {
        setCalibration(r);
        form.set({ whisper_cpu_threads: r.threads }); // saved with the rest of the page
      })
      .catch((e: Error) => setCalibrateError(e.message))
      .finally(() => setCalibrating(false));
  };

  const advancedRow = ({ key, title, hint, min, max }: (typeof ADVANCED)[number]) => (
    <Row key={key} title={title} hint={hint}>
      {() => (
        <DraftInput
          type="number"
          min={min}
          max={max}
          step="any"
          value={String(s[key])}
          onCommit={(raw) => {
            const n = Number(raw);
            if (raw.trim() !== "" && Number.isFinite(n) && n >= min && n <= max) form.set({ [key]: n });
          }}
        />
      )}
    </Row>
  );

  const parakeet = models.find((m) => m.id === s.parakeet_model);
  const customPause = pick(REPLY_WAIT, s.command_min_silence_ms) === undefined;

  const text = (key: keyof VoiceSettings, label: string, allowEmpty = false) => (
    <Row title={label}>
      {() => (
        <DraftInput
          type="text"
          value={String(v[key])}
          onCommit={(raw) => (allowEmpty || raw.trim()) && voice.set({ [key]: raw.trim() })}
        />
      )}
    </Row>
  );

  return (
    <SettingsPage forms={[form, voice, autostart]}>
      {general ? (
      <div className="sset__col">
        {autostart.supported && autostart.draft !== null && (
          <SettingsSection title="Startup" hint="When ekko runs.">
            <Row title="Start listening when I sign in" hint="ekko launches quietly with Windows." toggle>
              {(id) => <Switch labelId={id} checked={autostart.draft!} onChange={autostart.set} />}
            </Row>
          </SettingsSection>
        )}

        <SettingsSection title="What ekko can do" hint="Turn off anything you don’t want ekko doing.">
          {CAN_DO.map((r) => (
            <Row key={r.key} title={r.title} hint={r.hint} toggle>
              {(id) => <Switch labelId={id} checked={!s[r.key]} onChange={(on) => form.set({ [r.key]: !on })} />}
            </Row>
          ))}
        </SettingsSection>

        <SettingsSection title="Music" hint="Where “play …” and “queue …” go.">
          <Row title="Preferred music app" hint="Used when you don’t name an app. Saying “on Apple Music” still wins.">
            {(id) => <Segmented labelId={id} options={MUSIC_PLATFORMS} value={s.preferred_music_platform} onChange={(x) => form.set({ preferred_music_platform: x })} />}
          </Row>
        </SettingsSection>

        <SettingsSection title="Privacy" hint="What ekko keeps and checks.">
          <Row title="Keep audio clips" hint="Saves recordings of your requests on this laptop to help diagnose problems." toggle>
            {(id) => <Switch labelId={id} checked={!s.no_save} onChange={(on) => form.set({ no_save: !on })} />}
          </Row>
          <Row title="Only respond to my voice" hint="ekko ignores other people speaking nearby." toggle>
            {(id) => <Switch labelId={id} checked={!s.no_verify} onChange={(on) => form.set({ no_verify: !on })} />}
          </Row>
          <Row title="Write down what I say" hint="Shows your words in Chat. Turn off to keep no text record." toggle>
            {(id) => <Switch labelId={id} checked={!s.no_transcribe} onChange={(on) => form.set({ no_transcribe: !on })} />}
          </Row>
        </SettingsSection>
      </div>
      ) : (
      <>
      <div className="sset__col">
        <SettingsSection title="Hold to talk" hint="Optional. The fastest way to talk to ekko.">
          <Row
            title="Hold to talk"
            hint="Hold a key, speak, let go. No wake word and no waiting for a pause, so ekko starts working the moment you stop."
            toggle
          >
            {(id) => <Switch labelId={id} checked={s.push_to_talk} onChange={(on) => form.set({ push_to_talk: on })} />}
          </Row>
          <Row title="Key" hint="Click it, then press the combination you’d like to hold.">
            {(id) => (
              <HotkeyInput
                labelId={id}
                value={s.push_to_talk_hotkey}
                defaultValue={DEFAULT_HOLD_KEY}
                onChange={(chord) => form.set({ push_to_talk_hotkey: chord })}
              />
            )}
          </Row>
          <Row
            title="How to use it"
            hint={
              <>
                Hold <KeyCaps chord={s.push_to_talk_hotkey} />, say your request, then let go. It works from any app. The wake word and the other shortcuts keep working too.{" "}
                <HelpLink topic="hold-to-talk" onHelp={onHelp}>More about it</HelpLink>
              </>
            }
          >
            {() => null}
          </Row>
        </SettingsSection>

      </div>

      <div className="sset__col">
      <SettingsSection title="Listening" hint="How ekko hears you.">
        <Row title="Microphone" note={audio && !audio.available ? "Device list unavailable." : audio && !audio.volume ? "Volume control isn’t available for this device." : undefined}>
          {deviceSelect("input_device", "input", audio?.default_input)}
        </Row>
        <Row
          title="Wake word"
          hint="The phrase that gets ekko’s attention."
          note={
            <>
              A custom phrase needs a trained model first.{" "}
              <button type="button" className="panel__link" onClick={() => onHelp("wake-word")}>How to add one →</button>
            </>
          }
        >
          {() => <span className="panel__input panel__pending">“Hey ekko” <span className="srow__note">Locked</span></span>}
        </Row>
        <Row title="Wake sensitivity" hint="Waking by mistake? Go stricter. Having to repeat yourself? Go eager.">
          {(id) => <Segmented labelId={id} options={WAKE_SENSITIVITY} value={pick(WAKE_SENSITIVITY, s.wake_threshold)} onChange={(x) => form.set({ wake_threshold: x })} />}
        </Row>
        <Row title="After “Hey ekko”" hint="How ekko lets you know it’s listening. A chime or nothing gets you talking sooner than a spoken greeting.">
          {(id) => <Segmented labelId={id} options={ACK_STYLES} value={s.ack_style} onChange={(x) => form.set({ ack_style: x })} />}
        </Row>
        <Row
          title="Wait before replying"
          hint="How long a pause means you’ve finished talking."
          note={customPause ? `Set to ${s.command_min_silence_ms} ms under Advanced.` : undefined}
        >
          {(id) => <Segmented labelId={id} options={REPLY_WAIT} value={pick(REPLY_WAIT, s.command_min_silence_ms)} onChange={(x) => form.set({ command_min_silence_ms: x })} />}
        </Row>
        <Row title="Follow-up window" hint="After a reply, keep listening so you can carry on without the wake word.">
          {(id) => <Segmented labelId={id} options={FOLLOW_UP} value={pick(FOLLOW_UP, s.active_window_s)} onChange={(x) => form.set({ active_window_s: x })} />}
        </Row>
      </SettingsSection>

      <SettingsSection title="Speech recognition" hint="How ekko turns your voice into text.">
        <Row
          title="This computer"
          note={hw ? `${hw.gpu ? "A GPU was found." : "No GPU found, so ekko uses the processor."} ${hw.cores} processor cores.` : "Hardware check unavailable."}
        >
          {() => null}
        </Row>
        <Row
          title="Running now"
          note={
            running?.fallback_reason ? (
              <>
                <span className="panel__warning" title={running.fallback_reason}>
                  {explainFallback(running.fallback_reason, running.engine)}
                </span>{" "}
                {/CUDA unavailable/i.test(running.fallback_reason) && (
                  <HelpLink topic="stt-gpu" onHelp={onHelp}>Why?</HelpLink>
                )}
              </>
            ) : undefined
          }
        >
          {() => (
            <span className="srow__running">
              {running
                ? `${ENGINE_NAMES[running.engine]} · ${running.model} · ${running.device === "cuda" ? "GPU" : "CPU"}${running.threads ? ` · ${running.threads} threads` : ""}`
                : "Waiting for the listener to start…"}
            </span>
          )}
        </Row>
        <Row title="Recent requests">{() => <span className="srow__running">{describeRecent(recent)}</span>}</Row>
        <Row
          title="Engine"
          hint="Whisper uses the GPU when there is one. Parakeet is built for the processor. Automatic uses Whisper with a GPU and Parakeet without. Recent requests, above, shows how each does on your computer."
          note={<HelpLink topic="stt-engine" onHelp={onHelp}>Which should I pick?</HelpLink>}
        >
          {(id) => <Segmented labelId={id} options={ENGINE_CHOICES} value={s.stt_engine} onChange={(x) => form.set({ stt_engine: x })} />}
        </Row>
        {s.stt_engine !== "whisper" && (
          <Row
            title="Parakeet model"
            hint="Downloaded once to this computer, then works offline."
            note={
              downloadError ??
              (parakeet?.state === "error" ? parakeet.error : undefined) ??
              (parakeet?.downloaded && running && running.engine !== "parakeet" ? "Downloaded. Restart listening to start using it." : undefined)
            }
          >
            {(id) => (
              <span className="srow__stack">
                <select
                  className="panel__input"
                  aria-labelledby={id}
                  value={s.parakeet_model}
                  onChange={(e) => form.set({ parakeet_model: e.target.value })}
                >
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>{`${m.label} (${m.size_mb} MB)${m.downloaded ? " · downloaded" : ""}`}</option>
                  ))}
                  {!parakeet && <option value={s.parakeet_model}>{s.parakeet_model}</option>}
                </select>
                {parakeet?.state === "running" ? (
                  <span className="srow__progress">
                    <progress max={1} value={parakeet.progress} aria-label="Download progress" />
                    {`Downloading… ${Math.round(parakeet.progress * 100)}%`}
                  </span>
                ) : parakeet?.downloaded ? (
                  running && running.engine !== "parakeet" ? (
                    <button type="button" className="panel__button" disabled={restarting} onClick={useNow}>
                      {restarting ? "Restarting…" : "Restart listening to use it"}
                    </button>
                  ) : (
                    <span className="srow__note">Ready</span>
                  )
                ) : (
                  <button type="button" className="panel__button panel__button--primary" disabled={!parakeet} onClick={() => parakeet && startDownload(parakeet.id)}>
                    {parakeet?.state === "error" ? "Try again" : `Download (${parakeet?.size_mb ?? "…"} MB)`}
                  </button>
                )}
              </span>
            )}
          </Row>
        )}
        <Row
          title="Custom words"
          hint="Names ekko should always spell your way, separated by commas. Close mishearings are corrected."
        >
          {() => (
            <DraftInput
              type="text"
              value={s.custom_words.join(", ")}
              onCommit={(raw) =>
                form.set({ custom_words: [...new Set(raw.split(/[,\n]/).map((w) => w.trim()).filter(Boolean))] })
              }
            />
          )}
        </Row>
        <Row title="Whisper model" hint="Bigger is more accurate but slower. Automatic picks for this computer.">
          {(id) => (
            <select
              className="panel__input"
              aria-labelledby={id}
              value={s.whisper_model}
              onChange={(e) => form.set({ whisper_model: e.target.value as GeneralSettings["whisper_model"] })}
            >
              {WHISPER_MODELS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          )}
        </Row>
        <Row
          title="Processor"
          hint="Auto uses the GPU when there is one."
          note={hw && !hw.gpu && s.whisper_device === "gpu" ? "No GPU found; ekko will use the processor." : undefined}
        >
          {(id) => <Segmented labelId={id} options={PROCESSORS} value={s.whisper_device} onChange={(x) => form.set({ whisper_device: x })} />}
        </Row>
        <Row
          title="Processor threads"
          hint="0 lets ekko choose. Calibrate times this computer and picks the fastest."
          note={
            <>
              {calibrateError ??
                (calibration
                  ? `Fastest: ${calibration.threads} (${Object.entries(calibration.timings_ms).map(([n, ms]) => `${n} → ${ms} ms`).join(", ")}). Save to keep it.`
                  : hw ? `Automatic uses ${hw.auto_threads}.` : undefined)}{" "}
              <HelpLink topic="stt-calibrate" onHelp={onHelp}>What does Calibrate do?</HelpLink>
            </>
          }
        >
          {() => (
            <span className="srow__inline">
              <DraftInput
                type="number"
                min={0}
                max={256}
                step={1}
                value={String(s.whisper_cpu_threads)}
                onCommit={(raw) => /^\d+$/.test(raw.trim()) && Number(raw) <= 256 && form.set({ whisper_cpu_threads: Number(raw.trim()) })}
              />
              <button type="button" className="panel__button" disabled={calibrating} onClick={calibrate}>
                {calibrating ? "Timing… this takes a while" : "Calibrate"}
              </button>
            </span>
          )}
        </Row>
      </SettingsSection>

      <details className="sset__advanced">
        <summary>Advanced listening</summary>
        <SettingsSection title="Fine tuning" hint="Defaults suit most rooms. Change one at a time.">
          {ADVANCED.map(advancedRow)}
        </SettingsSection>
      </details>

      <SettingsSection title="ekko’s voice" hint="How ekko sounds when it answers.">
        <Row title="Speaker">{deviceSelect("output_device", "output", audio?.default_output)}</Row>
        <Row title="Voice" hint="Offline voices are free and private. Cloud voices sound more natural.">
          {(id) => (
            <select
              className="panel__input"
              aria-labelledby={id}
              value={v.tts_engine}
              onChange={(e) => voice.set({ tts_engine: e.target.value as VoiceSettings["tts_engine"] })}
            >
              {ENGINES.map((e) => (
                <option key={e.value} value={e.value}>{e.label}</option>
              ))}
            </select>
          )}
        </Row>
        {v.tts_engine === "kokoro" && text("kokoro_voice", "Kokoro voice")}
        {v.tts_engine === "chattts" && (
          <Row title="Voice seed" hint="Same number, same voice.">
            {() => (
              <DraftInput type="number" min={0} step={1} value={String(v.chattts_seed)} onCommit={(raw) => /^\d+$/.test(raw.trim()) && voice.set({ chattts_seed: Number(raw.trim()) })} />
            )}
          </Row>
        )}
        {v.tts_engine === "openai" && (
          <>
            {text("openai_tts_voice", "OpenAI voice")}
            {text("openai_tts_model", "OpenAI model")}
            {text("openai_tts_instructions", "Speaking style", true)}
          </>
        )}
        <Row title="Start speaking early" hint="Begin reading the answer aloud while the rest is still being written. Feels faster." toggle>
          {(id) => <Switch labelId={id} checked={v.tts_streaming} onChange={(on) => voice.set({ tts_streaming: on })} />}
        </Row>
      </SettingsSection>
      </div>
      </>
      )}
    </SettingsPage>
  );
}

export default AudioPanel;
