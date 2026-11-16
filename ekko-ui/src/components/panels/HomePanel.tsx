import "./Dashboard.css";
import "./Home.css";
import { useEffect, useState } from "react";
import {
  getChatDays, getChatHistory, getGeneralOverview, getReview, getWindow,
  type ChatDay, type ChatHistoryMessage, type ConversationWindow, type GeneralOverview, type ReviewItem,
} from "../../api/client";
import type { Section } from "../Sidebar";
import HoldToTalkTip from "./HoldToTalkTip";

const SUGGESTIONS = ["Summarise my unread email", "Why is my laptop slow?", "How much did I spend this week?"];
const hhmm = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

// Each user message paired with the assistant reply after it (that reply carries the stats).
function requestsFrom(msgs: ChatHistoryMessage[]) {
  return msgs.flatMap((m, i) => {
    const a = msgs[i + 1];
    if (m.role !== "user" || a?.role !== "assistant") return [];
    const s = a.stats;
    const tag = s?.error ? "FAILED" : s?.handled_by === "router" ? "LOCAL" : "CLOUD";
    const meta = ["chat", s?.provider ?? (tag === "LOCAL" ? "on device" : null), s?.cost_usd != null ? `$${s.cost_usd.toFixed(3)}` : null, s ? `${s.total_s.toFixed(1)}s` : null].filter(Boolean).join(" · ");
    return [{ id: m.id, time: hhmm(m.ts), text: m.text, meta, tag }];
  }).reverse().slice(0, 5);
}

// Backed by real endpoints: Needs you (memory review), Ask (hands off to Chat), Recent requests + Last conversations (chat history),
// Requests (overview), Memory (window). Everything else is grayed out until it has a backend.
export default function HomePanel({ onNavigate, onAsk }: { onNavigate: (s: Section) => void; onAsk: (text: string) => void }) {
  const [o, setO] = useState<GeneralOverview | null>(null);
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [later, setLater] = useState<string[]>([]);
  const [today, setToday] = useState<ChatHistoryMessage[] | null>(null);
  const [days, setDays] = useState<ChatDay[] | null>(null);
  const [win, setWin] = useState<ConversationWindow | null>(null);
  const [tab, setTab] = useState<"conv" | "tasks">("conv");
  const [draft, setDraft] = useState("");

  useEffect(() => {
    getGeneralOverview().then(setO).catch(() => {});
    getReview().then((r) => setItems(r.items)).catch(() => {});
    getChatHistory().then(setToday).catch(() => {});
    getChatDays().then(setDays).catch(() => {});
    getWindow().then(setWin).catch(() => {});
  }, []);

  function ask() {
    const text = draft.trim();
    if (text) onAsk(text);
  }

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const needs = (items ?? []).filter((it) => !later.includes(it.id));
  const n = needs.length;
  const summary = !o
    ? "Can't reach ekko's backend."
    : n > 0
      ? `${n} ${n === 1 ? "thing needs" : "things need"} you.`
      : "Nothing needs you right now.";
  const requests = today ? requestsFrom(today) : null;
  const pct = win ? Math.min(100, Math.round((win.tokens_est / win.budget) * 100)) : 0;

  return (
    <div className="dash">
      <HoldToTalkTip onNavigate={onNavigate} />
      <div className="hm">
        <div className="hm__top">
          <div className="hm__greet">
            <h1>{greeting}.</h1>
            <p>{summary}</p>
          </div>
          <div className={`hm__card hm__needs${n > 0 ? " hm__needs--on" : ""}`}>
            <div className="hm__row">
              <h3>Needs you{n > 0 && <span className="hm__pill">{n}</span>}</h3>
            </div>
            {items == null && <span className="hm__muted">Loading…</span>}
            {items && n === 0 && <span className="hm__muted">All caught up. New memories to review will show up here.</span>}
            <div>
              {needs.slice(0, 3).map((it) => (
                <div key={it.id} className="hm__need">
                  <div><b>{it.title}</b><span>{it.preview}</span></div>
                  <div className="hm__btns">
                    <button type="button" className="hm__btn" onClick={() => setLater([...later, it.id])}>Later</button>
                    <button type="button" className="hm__btn hm__btn--pri" onClick={() => onNavigate("Memory")}>Review</button>
                  </div>
                </div>
              ))}
            </div>
            {n > 3 && <span className="hm__mono">+{n - 3} more · <button type="button" className="hm__link" onClick={() => onNavigate("Memory")}>Review all →</button></span>}
          </div>
        </div>

        <div className="hm__section">
          <h3 style={{ fontSize: 20 }}>Ask ekko</h3>
          <div className="hm__card hm__ask">
            <textarea
              rows={2}
              value={draft}
              placeholder="Message ekko…"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }}
            />
            <div className="hm__bar">
              <span className="is-on">Ask</span>
              <span className="is-off" title="Coming soon">Context</span>
              <span className="is-off hm__opt" title="Coming soon">Do on my computer</span>
              <span className="is-off hm__opt" style={{ marginLeft: "auto", borderLeft: "1px solid var(--color-border)" }} title="Coming soon">Auto (chain) ▾</span>
              <button type="button" className="hm__send" disabled={!draft.trim()} onClick={ask}>Send</button>
            </div>
          </div>
          <div className="hm__chips">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="hm__chip" onClick={() => setDraft(s)}>{s}<i>↗</i></button>
            ))}
          </div>
        </div>

        <div className="hm__two">
          <section className="hm__card">
            <div className="hm__head">
              <h3>Recent requests</h3>
              <button type="button" className="hm__link" onClick={() => onNavigate("Usage")}>All activity →</button>
            </div>
            {requests == null && <div className="hm__empty">Loading…</div>}
            {requests?.length === 0 && <div className="hm__empty">Nothing asked today yet.</div>}
            {requests?.map((r) => (
              <div key={r.id} className="hm__req">
                <span className="hm__mono">{r.time}</span>
                <div className="hm__main"><b>{r.text}</b><span className="hm__mono">{r.meta}</span></div>
                <span className={`hm__tag hm__tag--${r.tag.toLowerCase()}`}>{r.tag}</span>
              </div>
            ))}
          </section>

          <section className="hm__card">
            <div className="hm__tabs">
              <div>
                <button type="button" className={`hm__tab${tab === "conv" ? " is-active" : ""}`} onClick={() => setTab("conv")}>Last conversations</button>
                <button type="button" className={`hm__tab${tab === "tasks" ? " is-active" : ""}`} onClick={() => setTab("tasks")}>Tasks</button>
              </div>
              {tab === "conv" && <button type="button" className="hm__link" onClick={() => onNavigate("Chat")}>Chat history →</button>}
            </div>
            {tab === "conv" && days == null && <div className="hm__empty" style={{ border: 0 }}>Loading…</div>}
            {tab === "conv" && days?.length === 0 && <div className="hm__empty" style={{ border: 0 }}>No conversations yet.</div>}
            {tab === "conv" && days?.slice(0, 4).map((d) => (
              <button key={d.day} type="button" className="hm__conv" onClick={() => onNavigate("Chat")}>
                <div className="hm__main"><b>{d.day}</b><span className="hm__mono">{d.count} messages</span></div>
              </button>
            ))}
            {tab === "tasks" && <div className="hm__empty is-off" style={{ border: 0 }}>Tasks aren't available yet.</div>}
          </section>
        </div>

        <div className="hm__stats">
          <button type="button" className="hm__card hm__stat hm__stat--link" onClick={() => onNavigate("Usage")}>
            <span style={{ fontSize: 15, fontWeight: 500 }}>Requests</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="hm__big">{o ? o.llm_total.calls.toLocaleString() : "—"}</span>
              <span className="hm__mono">cloud requests recorded</span>
            </div>
          </button>
          <section className="hm__card hm__mem">
            <div className="hm__row">
              <span style={{ fontSize: 15, fontWeight: 500 }}>Memory</span>
              <button type="button" className="hm__link" onClick={() => onNavigate("Memory")}>Open memory →</button>
            </div>
            <div className="hm__row" style={{ flexWrap: "wrap", alignItems: "baseline" }}>
              <span className="hm__big">{win ? `${win.turns.length} exchanges` : "—"}</span>
              {win && <span style={{ fontSize: 15 }}>~{win.tokens_est.toLocaleString()} / {win.budget.toLocaleString()} tokens <span className="hm__muted">({pct}%)</span></span>}
            </div>
            {win && <div className="hm__track"><div style={{ width: `${pct}%` }} /></div>}
          </section>
        </div>

        <section className="hm__card hm__mcp is-off" title="Coming soon">
          <div>
            <h3>Teach ekko new tricks with MCP servers</h3>
            <p>Plug in tools like GitHub, Notion or Linear, or point ekko at your own server. Not available yet.</p>
          </div>
          <div className="hm__btns">
            <button type="button" className="hm__btn" style={{ height: 36 }}>What is MCP?</button>
            <button type="button" className="hm__btn hm__btn--pri" style={{ height: 36 }}>Browse MCP servers</button>
          </div>
        </section>
      </div>
    </div>
  );
}
