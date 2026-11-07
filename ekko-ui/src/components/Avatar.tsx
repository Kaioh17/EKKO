import "./Avatar.css";
import { useEffect, useRef, useState } from "react";
import { getCurrentWindow, LogicalSize, PhysicalPosition, primaryMonitor } from "@tauri-apps/api/window";
import { subscribe } from "../api/socket";
import { acceptResearch, sendWake, type ResearchOffer } from "../api/client";
import { useCountdown } from "./useCountdown";

// The listener's state names -> the design's.
const STATE: Record<string, string> = { idle: "idle", listening: "listen", processing: "process", speaking: "respond", error: "sorry" };
const LABEL: Record<string, string> = { listening: "Listening", processing: "Thinking", speaking: "Speaking", error: "Couldn’t do it" };
const WAVE: Record<string, number> = { listen: 9, process: 8, respond: 9 }; // bars per state
const HEIGHTS = [8, 14, 10, 18, 12, 20, 9, 16, 12];
// Per-bar sensitivity so the bars don't move in lockstep.
const PROFILE = [0.55, 0.8, 0.65, 1, 0.75, 0.95, 0.6, 0.85, 0.7];
const NOISE_FLOOR = 0.03; // ambient mic noise reads as silence -> bars stay flat
const barScale = (level: number, i: number) =>
  Math.max(0.1, Math.min(1, Math.max(0, level - NOISE_FLOOR) * 1.6 * PROFILE[i % PROFILE.length]));
const HIDE_AFTER_IDLE_MS = 1500;
const MARGIN = 20;
const TASKBAR = 48;

export default function Avatar() {
  const [state, setState] = useState("idle");
  const [level, setLevel] = useState(0); // mic level 0-1, listening only
  const [answerInfo, setAnswerInfo] = useState<{ domain: string; model: string } | null>(null);
  const [transcript, setTranscript] = useState("");
  const [response, setResponse] = useState("");
  const [responseUntil, setResponseUntil] = useState<number | null>(null);
  const responseTimer = useRef<number | undefined>(undefined);
  const [offer, setOffer] = useState<ResearchOffer | null>(null); // Yes/No card, closes itself
  const [offerUntil, setOfferUntil] = useState<number | null>(null);
  const offerTimer = useRef<number | undefined>(undefined);

  function answerOffer(yes: boolean) {
    window.clearTimeout(offerTimer.current);
    if (yes && offer) acceptResearch(offer).catch(() => {});
    setOffer(null);
    setOfferUntil(null);
  }

  useEffect(() => {
    document.documentElement.classList.add("avatar-win");
    return subscribe((data) => {
      const m = data as { type?: string; state?: string; text?: string; timeout_s?: number; level?: number; domain?: string; model?: string; query?: string; urls?: string[] };
      if (m.type === "offer" && m.query && m.urls) {
        window.clearTimeout(offerTimer.current);
        const ms = (m.timeout_s ?? 5) * 1000;
        setOffer({ query: m.query, urls: m.urls });
        setOfferUntil(Date.now() + ms);
        offerTimer.current = window.setTimeout(() => { setOffer(null); setOfferUntil(null); }, ms);
      } else if (m.type === "level") {
        setLevel(m.level ?? 0);
      } else if (m.type === "meta") {
        setAnswerInfo({ domain: m.domain ?? "", model: m.model ?? "" });
      } else if (m.type === "status" && m.state) {
        setState(m.state);
        setLevel(0);
        if (m.state === "listening") { setTranscript(""); setAnswerInfo(null); }
      } else if (m.type === "transcript") {
        setTranscript(m.text ?? "");
      } else if (m.type === "response" || m.type === "clear_response") {
        window.clearTimeout(responseTimer.current);
        const text = m.type === "response" ? (m.text ?? "") : "";
        setResponse(text);
        const ms = (m.timeout_s ?? 20) * 1000;
        setResponseUntil(text ? Date.now() + ms : null);
        if (text) responseTimer.current = window.setTimeout(() => setResponse(""), ms);
      }
    });
  }, []);

  const left = useCountdown(response ? responseUntil : null);
  const offerLeft = useCountdown(offerUntil);
  const active = state !== "idle" || !!response || !!offer;
  const hasCard = active;
  const text = response || transcript;

  // Visible while active, hidden shortly after going idle; sized to its
  // content and parked bottom-right of the primary monitor.
  useEffect(() => {
    const win = getCurrentWindow();
    let cancelled = false;
    const t = window.setTimeout(async () => {
      if (cancelled) return;
      if (!active) return void win.hide();
      const w = 380, h = 240;
      await win.setSize(new LogicalSize(w, h));
      const mon = await primaryMonitor();
      if (mon) {
        const f = mon.scaleFactor;
        await win.setPosition(new PhysicalPosition(
          mon.position.x + mon.size.width - Math.round((w + MARGIN) * f),
          mon.position.y + mon.size.height - Math.round((h + MARGIN + TASKBAR) * f),
        ));
      }
      if (!cancelled) {
        await win.show();
        await win.setAlwaysOnTop(true); // re-assert: other windows can bump it
      }
    }, active ? 0 : HIDE_AFTER_IDLE_MS);
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [active, hasCard]);

  // The bar shows the answer's state while a response is up, even if the listener is idle.
  const bar = state === "idle" && response ? "respond" : (STATE[state] ?? "idle");
  const meta = response && left !== null ? `${left}s` : bar === "idle" ? "⌥ Space" : "";
  // Footer, flush under the bar/card. Processing: kind of thinking + model. Speaking: the model that answered.
  const foot = !answerInfo ? "" : bar === "process"
    ? [answerInfo.domain ? `Domain · ${answerInfo.domain}` : "General", answerInfo.model].filter(Boolean).join(" · ")
    : bar === "respond" ? answerInfo.model : "";
  return (
    <div className="av-dock">
      {hasCard && (
        <div className={`av-bar av-bar--${bar}`} key={bar} onClick={() => void sendWake()} title="Click to talk to ekko">
          <span className="av-bar__label">{response ? "Speaking" : (LABEL[state] ?? "ekko")}</span>
          <div className="av-wave" aria-hidden>
            {Array.from({ length: WAVE[bar] ?? 0 }, (_, i) => bar === "listen"
              ? <i key={i} style={{ transform: `scaleY(${barScale(level, i)})` }} />
              : <i key={i} style={{ height: HEIGHTS[i % HEIGHTS.length], animationDelay: `${(i * 0.07).toFixed(2)}s`, animationDuration: `${(0.6 + ((i * 37) % 7) / 10).toFixed(2)}s` }} />)}
          </div>
          <span className="av-bar__meta">{meta}</span>
        </div>
      )}
      {text && <div className="av-text">{text}</div>}
      {offer && (
        <div className="av-text">
          Research this further on Brave?{offerLeft !== null ? ` (${offerLeft}s)` : ""}
          <div className="av-offer">
            <button type="button" onClick={() => answerOffer(true)}>Yes</button>
            <button type="button" onClick={() => answerOffer(false)}>No</button>
          </div>
        </div>
      )}
      {foot && <div className={`av-tag${text ? " av-tag--card" : ""}`}>{foot}</div>}
    </div>
  );
}
