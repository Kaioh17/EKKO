import "./Dashboard.css";
import { useEffect, useState } from "react";
import { getGeneralOverview, getReview, sendChat, type GeneralOverview, type ReviewItem } from "../../api/client";
import type { Section } from "../Sidebar";
import HoldToTalkTip from "./HoldToTalkTip";

const QUICK: { label: string; hint: string; to: Section }[] = [
  { label: "Chat", hint: "Full conversation", to: "Chat" },
  { label: "Briefing", hint: "Your daily briefing", to: "Briefing" },
  { label: "Memory", hint: "What ekko remembers", to: "Memory" },
  { label: "Connections", hint: "Accounts and tools", to: "Connections" },
];

// Order follows the research: what needs you first, then the number you check (spend), then ask, then shortcuts.
// ponytail: "spent" is all recorded calls, same as Usage; tracking began this month. Add a month filter in the backend when history matters.
export default function HomePanel({ onNavigate }: { onNavigate: (s: Section) => void }) {
  const [o, setO] = useState<GeneralOverview | null>(null);
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [draft, setDraft] = useState("");
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    getGeneralOverview().then(setO).catch(() => {});
    getReview().then((r) => setItems(r.items)).catch(() => {});
  }, []);

  async function ask() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      setReply((await sendChat(text)).reply);
      setDraft("");
    } catch {
      setReply("Could not reach ekko.");
    } finally {
      setSending(false);
    }
  }

  const t = o?.llm_total;
  const local = o?.routing.match_rate == null ? null : Math.round(o.routing.match_rate * 100);
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const n = items?.length ?? 0;
  const summary = !o
    ? "Can't reach ekko's backend."
    : n > 0
      ? `${n} ${n === 1 ? "memory needs" : "memories need"} your review.`
      : "Nothing needs you right now.";
  const stats = [
    { label: "Spent this month", value: t ? `$${(t.cost_usd ?? 0).toFixed(2)}` : "—", sub: t ? `${t.calls.toLocaleString()} requests` : "" },
    { label: "Answered on-device", value: local == null ? "—" : `${local}%`, sub: "free and private" },
  ];

  return (
    <div className="dash">
      <div className="dash__head">
        <h2>{greeting}.</h2>
        <p>{summary}</p>
      </div>

      <HoldToTalkTip onNavigate={onNavigate} />

      <div className="dash__split dash__split--even">
        <div className="dash__card dash__card--wide">
          <div className="dash__title-row">
            <h3 className="dash__title">Needs you</h3>
            {n > 0 && <button type="button" className="panel__button" onClick={() => onNavigate("Memory")}>Review all</button>}
          </div>
          {items == null && <span className="dash__sub">Loading…</span>}
          {items && n === 0 && <span className="dash__kicker">All caught up. New memories to review will show up here.</span>}
          {items?.slice(0, 3).map((it) => (
            <button key={it.id} type="button" className="home__item" onClick={() => onNavigate("Memory")}>
              <b>{it.title}</b>
              <span className="dash__sub">{it.preview}</span>
            </button>
          ))}
          {n > 3 && <span className="dash__sub">+{n - 3} more</span>}
        </div>
        <div className="dash__card dash__card--wide">
          <h3 className="dash__title">Ask ekko</h3>
          <input
            className="home__input"
            value={draft}
            placeholder="Type a quick question…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void ask()}
            disabled={sending}
          />
          {(sending || reply) && <span className="home__reply">{sending ? "…" : reply}</span>}
          {reply && !sending && <button type="button" className="panel__button" onClick={() => onNavigate("Chat")}>Continue in Chat</button>}
        </div>
      </div>

      <div className="dash__grid4">
        {stats.map((s) => (
          <div className="dash__card" key={s.label}>
            <span className="dash__kicker">{s.label}</span>
            <span className="dash__big">{s.value}</span>
            <span className="dash__sub">{s.sub}</span>
          </div>
        ))}
        <button type="button" className="dash__card home__link" style={{ gridColumn: "span 2" }} onClick={() => onNavigate("Usage")}>
          <span className="dash__kicker">Usage</span>
          <span className="dash__sub">See spend by model and month →</span>
        </button>
      </div>

      <div className="dash__grid4">
        {QUICK.map((q) => (
          <button key={q.to} type="button" className="dash__card home__link" onClick={() => onNavigate(q.to)}>
            <b>{q.label}</b>
            <span className="dash__sub">{q.hint}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
