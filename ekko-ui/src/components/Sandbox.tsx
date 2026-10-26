import "./Avatar.css"; // shared keyframes and the transparent-window rule
import "./Sandbox.css";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { getCurrentWindow, LogicalSize, PhysicalPosition, primaryMonitor } from "@tauri-apps/api/window";
import { openUrl } from "@tauri-apps/plugin-opener";
import { subscribe } from "../api/socket";
import { safeUrl } from "../api/safeUrl";
import { useCountdown } from "./useCountdown";
import AvatarMark from "./AvatarMark";

// Where every on-screen report lands (system checks, battery, daily briefing).
// Replaces the old terminal windows: the report scripts push their data to
// the backend, which relays it here on /ws/status as type "report".

interface Block {
  kind: string;
  title: string;
  payload: Record<string, any>;
}
interface Session {
  key: string;
  title: string;
  blocks: Block[];
}

const CLOSE_AFTER_S = 60; // idle time before the sandbox closes itself
const BRIEFING_SECTIONS = 3; // system, news, stocks
const DEFAULT_READ = 2; // news stories shown before "Show more"
const MARGIN = 20;
const TASKBAR = 48;
const AVATAR_ROOM = 100;
const strip = (s: string) => s.replace(/\[\/?[a-z ]+\]/g, "");

function Health({ payload }: { payload: Block["payload"] }) {
  const metrics: { name: string; detail: string; flagged?: boolean }[] = payload.metrics ?? [];
  return (
    <>
      {metrics.map((m, i) => (
        <div className={`sb__row${m.flagged ? " sb__row--flag" : ""}`} key={i}>
          <b>{m.name}</b>
          <span>{m.detail}</span>
        </div>
      ))}
      {payload.summary && <div className="sb__note">{payload.summary}</div>}
    </>
  );
}

function News({ payload }: { payload: Block["payload"] }) {
  const [all, setAll] = useState(false);
  const items: { title: string; points: number; url: string }[] = payload.items ?? [];
  const read: number = payload.read ?? DEFAULT_READ;
  if (!items.length) return <div className="sb__empty">No news fetched.</div>;
  return (
    <>
      {(all ? items : items.slice(0, read)).map((it, i) => (
        <div className="sb__news" key={i}>
          <small>{it.points} points{i < read ? " · ekko is reading" : ""}</small>
          {safeUrl(it.url) ? (
            <a href={it.url} onClick={(e) => { e.preventDefault(); void openUrl(safeUrl(it.url)!); }}>{it.title}</a>
          ) : (
            <span>{it.title}</span>
          )}
        </div>
      ))}
      {items.length > read && (
        <button type="button" className="sb__more" onClick={() => setAll(!all)}>
          {all ? "Show less" : `Show more (${items.length - read})`}
        </button>
      )}
    </>
  );
}

function Stocks({ payload }: { payload: Block["payload"] }) {
  const items: { label: string; price: number | null; change_pct: number | null; error: string | null; take: string }[] = payload.items ?? [];
  if (!items.length) return <div className="sb__empty">No tickers configured.</div>;
  return (
    <>
      {items.map((s, i) => (
        <div className="sb__stock" key={i}>
          <b>{strip(s.label)}</b>
          <span>{s.price == null ? "unavailable" : `$${s.price.toFixed(2)}`}</span>
          <span>{s.change_pct == null ? "" : `${s.change_pct >= 0 ? "▲" : "▼"} ${Math.abs(s.change_pct).toFixed(1)}%`}</span>
          {(s.take || s.error) && <small>{s.take || s.error}</small>}
        </div>
      ))}
    </>
  );
}

const LABEL: Record<string, string> = { metrics: "Computer health", news: "Tech news", stocks: "Watchlist" };
const BODY: Record<string, (p: { payload: Block["payload"] }) => React.ReactElement> = { metrics: Health, news: News, stocks: Stocks };

export default function Sandbox() {
  const [session, setSession] = useState<Session | null>(null);
  const [wide, setWide] = useState(false);
  const nextId = useRef(0);

  useEffect(() => {
    document.documentElement.classList.add("avatar-win");
    return subscribe((data) => {
      const m = data as { type?: string; kind?: string; title?: string; payload?: Block["payload"] };
      if (m.type !== "report" || !m.kind) return;
      const block: Block = { kind: m.kind, title: m.title ?? "", payload: m.payload ?? {} };
      const briefing = block.title.includes("Daily Briefing");
      const key = briefing ? "briefing" : String(nextId.current++);
      setSession((cur) => {
        if (briefing && cur?.key === "briefing") {
          return { ...cur, blocks: [...cur.blocks.filter((b) => b.kind !== block.kind), block] };
        }
        return { key, title: briefing ? "Daily briefing" : block.title.replace(/^EKKO\s*—\s*/, ""), blocks: [block] };
      });
    });
  }, []);

  const open = session !== null;
  const shown = useRef(false);
  const [until, setUntil] = useState<number | null>(null);
  const [hover, setHover] = useState(false);

  // Close by itself a while after the last report/interaction; paused while the
  // pointer is over it, and not started while a briefing is still writing.
  const writing = session !== null && session.key === "briefing" && session.blocks.length < BRIEFING_SECTIONS;
  useEffect(() => {
    if (!open || writing || hover) return void setUntil(null);
    const deadline = Date.now() + CLOSE_AFTER_S * 1000;
    setUntil(deadline);
    const id = window.setTimeout(() => setSession(null), CLOSE_AFTER_S * 1000);
    return () => window.clearTimeout(id);
  }, [open, writing, hover, session]);
  const left = useCountdown(until);

  useEffect(() => {
    const win = getCurrentWindow();
    if (!open) {
      shown.current = false;
      return void win.hide();
    }
    const w = wide ? 740 : 460, h = 600;
    void (async () => {
      if (shown.current) {
        // Expanding/shrinking: keep the bottom-right corner where it is.
        const [pos, size] = await Promise.all([win.outerPosition(), win.outerSize()]);
        const f = await win.scaleFactor();
        await win.setSize(new LogicalSize(w, h));
        await win.setPosition(new PhysicalPosition(pos.x + size.width - Math.round(w * f), pos.y + size.height - Math.round(h * f)));
        return;
      }
      await win.setSize(new LogicalSize(w, h));
      const mon = await primaryMonitor();
      if (mon) {
        const f = mon.scaleFactor;
        await win.setPosition(new PhysicalPosition(
          mon.position.x + mon.size.width - Math.round((w + MARGIN) * f),
          mon.position.y + mon.size.height - Math.round((h + MARGIN + TASKBAR + AVATAR_ROOM) * f),
        ));
      }
      await win.show();
      shown.current = true;
    })();
  }, [open, wide]);

  function drag(e: MouseEvent) {
    if ((e.target as HTMLElement).closest("button")) return;
    void getCurrentWindow().startDragging();
  }

  if (!session) return null;
  const status = writing ? `Writing ${session.blocks.length} of ${BRIEFING_SECTIONS}…` : hover ? "Paused while hovering" : left === null ? "Up to date" : `Closes in ${left}s`;
  const order = ["metrics", "news", "stocks"];
  const blocks = [...session.blocks].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));

  return (
    <div className={`sb-frame`}>
      <div className={`sb${wide ? " sb--wide" : ""}`} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <div className="sb__head" onMouseDown={drag}>
          <div className="sb__mark"><AvatarMark state={writing ? "respond" : "idle"} size={22} /></div>
          <div className="sb__titles">
            <span className="sb__title">{session.title}</span>
            <span className="sb__status"><i style={{ background: writing ? "#10C172" : "#C9CDC4" }} />{status}</span>
          </div>
          <button type="button" className="sb__icon" title={wide ? "Make narrow" : "Make wide"} onClick={() => setWide(!wide)}>{wide ? "⇲" : "⤢"}</button>
          <button type="button" className="sb__icon" title="Close" onClick={() => setSession(null)}>✕</button>
        </div>
        <div className="sb__body">
          {blocks.map((b) => {
            const Body = BODY[b.kind];
            return (
              <div className={`sb__block${b.kind === "news" ? " sb__block--span" : ""}`} key={b.kind + session.key}>
                <div className="sb__label">{LABEL[b.kind] ?? b.kind}</div>
                {Body ? <Body payload={b.payload} /> : <div className="sb__empty">Nothing to show.</div>}
              </div>
            );
          })}
        </div>
        {/* grayed out: follow-up questions about a report have no backend yet */}
        <div className="sb__foot"><input disabled placeholder="Ask about this…" /></div>
      </div>
    </div>
  );
}
