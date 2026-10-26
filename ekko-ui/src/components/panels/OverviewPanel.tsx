import "./Dashboard.css";
import { useEffect, useState } from "react";
import { getGeneralOverview, type GeneralOverview } from "../../api/client";
import type { EkkoStatus } from "../TopBar";

// Monthly history and spend caps are not tracked by the backend yet: that
// card is shown grayed out, empty.
const MONTHS = ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"];
const LIVE_DOT: Record<EkkoStatus, string> = { idle: "#C9CDC4", listening: "#3D8BFF", processing: "#FFC83D", speaking: "#10C172", error: "#000" };

const n = (v: number) => v.toLocaleString();

export default function OverviewPanel({ status }: { status: EkkoStatus }) {
  const [o, setO] = useState<GeneralOverview | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    getGeneralOverview().then(setO).catch(() => setO(null)).finally(() => setLoaded(true));
  }, []);

  const t = o?.llm_total;
  const spent = t?.cost_usd ?? 0;
  const providers = [...(o?.llm_providers ?? [])].sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0));
  const maxCost = Math.max(...providers.map((p) => p.cost_usd ?? 0), 0.0001);
  const stats = [
    { label: "Spent", value: `$${spent.toFixed(2)}`, sub: "all recorded LLM calls" },
    { label: "Tokens", value: t ? n(t.tokens) : "—", sub: t ? `${n(t.cached_tokens)} cached` : "" },
    { label: "Requests", value: t ? n(t.calls) : "—", sub: o?.routing.match_rate == null ? "" : `${Math.round(o.routing.match_rate * 100)}% matched locally` },
    { label: "Avg response", value: t?.avg_latency_s == null ? "—" : `${t.avg_latency_s.toFixed(1)}s`, sub: "LLM calls" },
  ];
  const cards = [
    { label: "Listener", value: status, dot: LIVE_DOT[status] },
    { label: "Backend", value: !loaded ? "…" : o ? "connected" : "unreachable", dot: o ? "#000" : "#ff5f56" },
    { label: "Last answered by", value: o?.routing.last_intent ?? "no routes yet", dot: "#000" },
    { label: "Memory", value: o ? `${o.memory.accepted} saved · ${o.memory.pending} waiting` : "—", dot: o?.memory.pending ? "#FFC83D" : "#000" },
  ];

  return (
    <div className="dash">
      <div className="dash__head"><h2>Overview</h2><p>What ekko has done, and what it cost.</p></div>
      <div className="dash__grid4">
        {stats.map((s) => (
          <div className="dash__card" key={s.label}>
            <span className="dash__kicker">{s.label}</span>
            <span className="dash__big">{s.value}</span>
            <span className="dash__sub">{s.sub}</span>
          </div>
        ))}
      </div>
      <div className="dash__split">
        <div className="dash__card is-off" style={{ gap: 14 }} title="Not available yet">
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="dash__kicker">Monthly spend · last 12 months</span>
            <span className="dash__mock">not tracked yet</span>
          </div>
          <div className="dash__chart">
            {MONTHS.map((m) => (
              <div className="dash__chart-col" key={m}><div style={{ height: 24, background: "#C9CDC4" }} /></div>
            ))}
          </div>
          <div className="dash__days">{MONTHS.map((m) => <span key={m}>{m}</span>)}</div>
        </div>
        <div className="dash__card" style={{ gap: 14 }}>
          <span className="dash__kicker">By model</span>
          {providers.length === 0 && <span className="dash__sub">No LLM calls yet.</span>}
          {providers.map((p) => (
            <div className="dash__row" key={p.provider}>
              <div className="dash__row-top"><b>{p.provider}</b><span>${(p.cost_usd ?? 0).toFixed(2)}</span></div>
              <div className="dash__bar"><div style={{ width: `${((p.cost_usd ?? 0) / maxCost) * 100}%` }} /></div>
              <span className="dash__mock">{n(p.tokens)} tokens · {n(p.calls)} calls</span>
            </div>
          ))}
        </div>
      </div>
      <div className="dash__grid4">
        {cards.map((c) => (
          <div className="dash__card dash__card--line" key={c.label}>
            <div className="dash__status"><span className="status-dot" style={{ background: c.dot }} />{c.label}</div>
            <span className="dash__sub">{c.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
