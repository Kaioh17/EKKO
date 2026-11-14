import { useEffect, useState } from "react";
import { getSettings, patchSettings, type GeneralSettings } from "../../api/client";
import type { Section } from "../Sidebar";
import { KeyCaps } from "./HotkeyInput";

const DISMISSED = "ekko.tip.hold-to-talk";

/** Shown on Home until hold-to-talk is on or the tip is dismissed: the feature is
 *  optional and off by default, so this is how most people find out it exists. */
export default function HoldToTalkTip({ onNavigate }: { onNavigate: (s: Section) => void }) {
  const [s, setS] = useState<GeneralSettings | null>(null);
  const [gone, setGone] = useState(() => localStorage.getItem(DISMISSED) === "1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSettings<GeneralSettings>("general").then(setS).catch(() => undefined);
  }, []);

  if (!s || gone) return null;
  if (s.push_to_talk) {
    return (
      <div className="tip tip--done" role="status">
        <b>Hold to talk is on.</b>
        <span>
          Hold <KeyCaps chord={s.push_to_talk_hotkey} />, say your request, then let go.
        </span>
        <button type="button" className="panel__button" onClick={() => setGone(true)}>Got it</button>
      </div>
    );
  }

  const turnOn = async () => {
    setBusy(true);
    setError(null);
    try {
      setS(await patchSettings<GeneralSettings>("general", { push_to_talk: true }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const dismiss = () => {
    localStorage.setItem(DISMISSED, "1");
    setGone(true);
  };

  return (
    <div className="tip" role="note">
      <b>Faster: hold to talk</b>
      <span>
        Hold <KeyCaps chord={s.push_to_talk_hotkey} />, speak, let go. No wake word, no waiting for a pause. It’s optional and you can pick your own keys.
      </span>
      {error && <span className="panel__warning">{error}</span>}
      <span className="tip__actions">
        <button type="button" className="panel__button panel__button--primary" disabled={busy} onClick={() => void turnOn()}>
          {busy ? "Turning on…" : "Turn it on"}
        </button>
        <button type="button" className="panel__button" onClick={() => onNavigate("Audio")}>Choose keys</button>
        <button type="button" className="panel__link" onClick={dismiss}>Not now</button>
      </span>
    </div>
  );
}
