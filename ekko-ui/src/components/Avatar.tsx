import "./Avatar.css";
import { useEffect, useRef, useState } from "react";
import { getCurrentWindow, LogicalSize, PhysicalPosition, primaryMonitor } from "@tauri-apps/api/window";
import { subscribe } from "../api/socket";
import { sendWake } from "../api/client";
import { useCountdown } from "./useCountdown";
import AvatarMark, { COLOR, type MarkState } from "./AvatarMark";

// The listener's state names -> the design's. Emotions are not assigned yet,
// so the eyes stay on the default.
const STATE: Record<string, MarkState> = { idle: "idle", listening: "listen", processing: "process", speaking: "respond", error: "idle" };
const LABEL: Record<string, string> = { listening: "Listening", processing: "Thinking", speaking: "Answer", error: "Error" };
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
      const w = hasCard ? 460 : 100, h = hasCard ? 320 : 100;
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

  const mark = STATE[state] ?? "idle";
  const color = COLOR[mark];
  return (
    <div className="av-dock">
      {hasCard && (
        <div className="av-card" key={state + (response ? "r" : "")} style={{ border: `1.5px solid ${color}` }}>
          <div className="av-card-label" style={{ color }}><i />{response ? "Answer" : (LABEL[state] ?? "ekko")}{response && left !== null && <span className="av-card-count">closes in {left}s</span>}</div>
          {text && <div className="av-card-text">{text}</div>}
        </div>
      )}
      <div
        className="av-orb"
        title="Click to talk to ekko"
        onClick={() => void sendWake()}
        style={{ boxShadow: `0 0 0 1.5px ${mark === "idle" ? "#FFFFFF33" : color}, 0 10px 28px ${color}${mark === "idle" ? "00" : "66"}` }}
      >
        <AvatarMark state={mark} size={58} />
      </div>
    </div>
  );
}
