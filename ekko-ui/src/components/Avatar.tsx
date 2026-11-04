import "./Avatar.css";
import { useEffect, useRef, useState } from "react";
import { getCurrentWindow, LogicalSize, PhysicalPosition, primaryMonitor } from "@tauri-apps/api/window";
import { subscribe } from "../api/socket";
import { sendWake } from "../api/client";
import { useCountdown } from "./useCountdown";

// The listener's state names -> the design's.
const STATE: Record<string, string> = { idle: "idle", listening: "listen", processing: "process", speaking: "respond", error: "sorry" };
const LABEL: Record<string, string> = { listening: "Listening", processing: "Thinking", speaking: "Speaking", error: "Couldn’t do it" };
const WAVE: Record<string, number> = { listen: 9, process: 8, respond: 9 }; // bars per state
const HEIGHTS = [8, 14, 10, 18, 12, 20, 9, 16, 12];
const HIDE_AFTER_IDLE_MS = 1500;
const MARGIN = 20;
const TASKBAR = 48;

export default function Avatar() {
  const [state, setState] = useState("idle");
  const [transcript, setTranscript] = useState("");
  const [response, setResponse] = useState("");
  const [responseUntil, setResponseUntil] = useState<number | null>(null);
  const responseTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    document.documentElement.classList.add("avatar-win");
    return subscribe((data) => {
      const m = data as { type?: string; state?: string; text?: string; timeout_s?: number };
      if (m.type === "status" && m.state) {
        setState(m.state);
        if (m.state === "listening") setTranscript("");
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
  const active = state !== "idle" || !!response;
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
      if (!cancelled) await win.show();
    }, active ? 0 : HIDE_AFTER_IDLE_MS);
    return () => { cancelled = true; window.clearTimeout(t); };
  }, [active, hasCard]);

  // The bar shows the answer's state while a response is up, even if the listener is idle.
  const bar = state === "idle" && response ? "respond" : (STATE[state] ?? "idle");
  const meta = response && left !== null ? `${left}s` : bar === "idle" ? "⌥ Space" : "";
  return (
    <div className="av-dock">
      {hasCard && (
        <div className={`av-bar av-bar--${bar}`} key={bar} onClick={() => void sendWake()} title="Click to talk to ekko">
          <span className="av-bar__label">{response ? "Speaking" : (LABEL[state] ?? "ekko")}</span>
          <div className="av-wave" aria-hidden>
            {Array.from({ length: WAVE[bar] ?? 0 }, (_, i) => <i key={i} style={{ height: HEIGHTS[i % HEIGHTS.length], animationDelay: `${(i * 0.07).toFixed(2)}s`, animationDuration: `${(0.6 + ((i * 37) % 7) / 10).toFixed(2)}s` }} />)}
          </div>
          <span className="av-bar__meta">{meta}</span>
        </div>
      )}
      {text && <div className="av-text">{text}</div>}
    </div>
  );
}
