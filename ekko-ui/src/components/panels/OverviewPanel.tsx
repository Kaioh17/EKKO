import "./Dashboard.css";
import { useEffect, useState } from "react";
import { getGeneralOverview, getProviders, type GeneralOverview, type ProviderInfo } from "../../api/client";
import ProviderLogo from "../ProviderLogo";
import type { EkkoStatus } from "../TopBar";

const MONTHS = Array.from({ length: 12 }, (_, i) => new Date(new Date().getFullYear(), new Date().getMonth() - 11 + i, 1).toLocaleString("en", { month: "short" }));
const LIVE_DOT: Record<EkkoStatus, string> = { idle: "#D4D4D4", listening: "#3D8BFF", processing: "#FFC83D", speaking: "#10C172", error: "#111" };

const n = (v: number) => v.toLocaleString();

const where = (i?: ProviderInfo) => (i?.kind === "Local" ? "on your laptop" : i?.kind === "Free API" ? "cloud · free tier" : "cloud");

export default function OverviewPanel({ status, advanced = false }: { status: EkkoStatus; advanced?: boolean }) {
  const [info, setInfo] = useState<ProviderInfo[]>([]);
  useEffect(() => { getProviders().then(setInfo).catch(() => {}); }, []);
  const [o, setO] = useState<GeneralOverview | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    getGeneralOverview().then(setO).catch(() => setO(null)).finally(() => setLoaded(true));
  }, []);

  const t = o?.llm_total;
  const spent = t?.cost_usd ?? 0;
  const providers = [...(o?.llm_providers ?? [])].sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0));
  const maxCost = Math.max(...providers.map((p) => p.cost_usd ?? 0), 0.0001);
  const local = o?.routing.match_rate == null ? null : Math.round(o.routing.match_rate * 100);
  const scores = o?.routing.recent_scores ?? [];
  const th = o?.routing.threshold ?? 0;
  const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  const stats = [
    { label: "Spent", value: `$${spent.toFixed(2)}`, sub: "this month" },
    { label: "Requests", value: t ? n(t.calls) : "—", sub: advanced && t ? `${n(t.tokens)} tokens · ${n(t.cached_tokens)} cached` : "things you asked" },
    { label: "Answered on-device", value: local == null ? "—" : `${local}%`, sub: "free and private" },
    { label: "Avg response", value: t?.avg_latency_s == null ? "—" : `${t.avg_latency_s.toFixed(1)}s`, sub: "from asking to answer" },
  ];
  const cards = [
    { label: "Listener", value: status, dot: LIVE_DOT[status] },
    { label: "Backend", value: !loaded ? "…" : o ? "connected" : "unreachable", dot: o ? "#111" : "#111" },
    { label: "Last answered by", value: o?.routing.last_intent ?? "no routes yet", dot: "#111" },
    { label: "Memory", value: o ? `${o.memory.accepted} saved · ${o.memory.in_window} in conversation` : "—", dot: "#111" },
  ];

  return (
    <div className="dash">
      <div className="dash__head">
        <h2>${spent.toFixed(2)} spent on {t ? n(t.calls) : 0} requests.{local != null && ` ${local}% never left your laptop.`}</h2>
        <p>Requests ekko can match on its own run locally for free. Everything else goes to a cloud model.</p>
      </div>
      <div className="dash__grid4 dash__grid4--tint">
        {stats.map((s) => (
          <div className="dash__card" key={s.label}>
            <span className="dash__kicker">{s.label}</span>
            <span className="dash__big">{s.value}</span>
            <span className="dash__sub">{s.sub}</span>
          </div>
        ))}
      </div>
      <div className="dash__split dash__split--even">
        <div className="dash__card dash__card--wide">
          <div className="dash__title-row"><h3 className="dash__title">Where it went</h3><span className="dash__sub">by model</span></div>
          {providers.length === 0 && <span className="dash__sub">No LLM calls yet.</span>}
          {providers.map((p) => {
            const i = info.find((x) => x.name === p.provider);
            return (
              <div className="dash__row" key={p.provider}>
                <div className="dash__row-top">
                  <b style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <ProviderLogo name={p.provider} />{i?.label ?? p.provider}
                    <span className="dash__where">{where(i)}</span>
                  </b>
                  <span>${(p.cost_usd ?? 0).toFixed(2)}</span>
                </div>
                <div className="dash__bar"><div style={{ width: `${((p.cost_usd ?? 0) / maxCost) * 100}%` }} /></div>
                <span className="dash__mock">{n(p.calls)} requests{advanced && ` · ${n(p.tokens)} tokens`}</span>
              </div>
            );
          })}
        </div>
        <div className={advanced ? "dash__stack" : undefined} style={advanced ? undefined : { display: "contents" }}>
          <div className={advanced ? "dash__stack-in" : "dash__card dash__card--wide"} style={advanced ? undefined : { display: "contents" }}>
            <div className="dash__card dash__card--wide">
              <h3 className="dash__title">Monthly spend</h3>
              <div className="dash__chart">
                {MONTHS.map((_, k) => (
                  <div className="dash__chart-col" key={k}>
                    <div style={{ height: k === 11 ? `${spent > 0 ? 100 : 2}%` : 2, background: k === 11 ? "var(--color-fill)" : "var(--color-border)" }} />
                  </div>
                ))}
              </div>
              <span className="dash__kicker">Tracking began this month, so there’s one bar. Next month you’ll be able to compare.</span>
              {!advanced && <span className="dash__kicker dash__hint">Switch to Advanced to see token counts and how ekko decides between local and cloud.</span>}
            </div>
            {advanced && o && (
              <div className="dash__card dash__card--wide">
                <div className="dash__title-row">
                  <h3 className="dash__title">Routing confidence</h3>
                  <span className="dash__sub">last {scores.length} · threshold {th.toFixed(2)}</span>
                </div>
                {scores.length === 0 && <span className="dash__sub">No routed requests yet.</span>}
                <div className="dash__chart dash__chart--dense">
                  {scores.length > 0 && <div style={{ position: "absolute", left: 0, right: 0, bottom: `${th * 100}%`, borderTop: "1px dashed var(--color-text-muted)" }} />}
                  {scores.map((s, k) => (
                    <div className="dash__chart-col" key={k} title={s.toFixed(3)}>
                      <div style={{ height: `${Math.max(s, 0.02) * 100}%`, background: s >= th ? "var(--color-fill)" : "var(--color-border)" }} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      {advanced && o && (
        <>
          <div className="dash__grid4">
            <div className="dash__card dash__card--wide" style={{ gridColumn: "1 / -1" }}>
              <h3 className="dash__title">Analytics</h3>
              {[
                ["Average confidence", avg == null ? "—" : avg.toFixed(2)],
                ["Lowest confidence", scores.length ? Math.min(...scores).toFixed(2) : "—"],
                ["Local match rate", local == null ? "—" : `${local}%`],
                ["Last route", `${o.routing.last_intent ?? "none"}${o.routing.last_status ? ` (${o.routing.last_status})` : ""}`],
                ["Cache hit rate", t && t.tokens ? `${Math.round((t.cached_tokens / t.tokens) * 100)}%` : "—"],
                ["Cost per request", t && t.calls ? `$${(spent / t.calls).toFixed(4)}` : "—"],
                ["Memory accepted / rejected", `${o.memory.accepted} / ${o.memory.rejected}`],
              ].map(([k, v]) => (
                <div className="dash__row-top" key={k}><span style={{ font: "inherit" }}>{k}</span><span>{v}</span></div>
              ))}
            </div>
          </div>
        </>
      )}
      {advanced && (
        <div className="dash__grid4">
          {cards.map((c) => (
            <div className="dash__card dash__card--line" key={c.label}>
              <div className="dash__status"><span className="status-dot" style={{ background: c.dot }} />{c.label}</div>
              <span className="dash__sub">{c.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
