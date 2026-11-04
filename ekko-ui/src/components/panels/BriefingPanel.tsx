import "./Panels.css";
import "./Dashboard.css";
import "./BriefingPanel.css";
import { openUrl } from "@tauri-apps/plugin-opener";
import { safeUrl } from "../../api/safeUrl";
import { useReportsSocket, type Report } from "../../hooks/useReportsSocket";

interface Metric {
  name: string;
  detail: string;
  flagged?: boolean;
}

interface NewsItem {
  title: string;
  points: number;
  url: string;
}

interface StockItem {
  ticker: string;
  label: string;
  price: number | null;
  change_pct: number | null;
  error: string | null;
  take: string;
}

function MetricsReport({ payload }: { payload: Record<string, unknown> }) {
  const metrics = (payload.metrics as Metric[] | undefined) ?? [];
  const summary = payload.summary as string | null | undefined;
  return (
    <>
      <table className="panel__table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric, i) => (
            <tr key={i}>
              <td>{metric.name}</td>
              <td style={{ color: metric.flagged ? "#ff5f56" : "#34c759" }}>{metric.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {summary && <p className="panel__value">{summary}</p>}
    </>
  );
}

function NewsReport({ payload }: { payload: Record<string, unknown> }) {
  const items = (payload.items as NewsItem[] | undefined) ?? [];
  if (!items.length) return <p className="panel__empty">No news fetched.</p>;
  return (
    <ul className="panel__list">
      {items.map((item, i) => (
        <li key={i} className="panel__list-item">
          {safeUrl(item.url) ? (
            <a
              href={safeUrl(item.url)!}
              onClick={(e) => {
                e.preventDefault();
                void openUrl(safeUrl(item.url)!);
              }}
            >
              {item.title}
            </a>
          ) : (
            <span>{item.title}</span>
          )}
          <span>{item.points} pts</span>
        </li>
      ))}
    </ul>
  );
}

function StocksReport({ payload }: { payload: Record<string, unknown> }) {
  const items = (payload.items as StockItem[] | undefined) ?? [];
  if (!items.length) return <p className="panel__empty">No tickers configured.</p>;
  return (
    <ul className="panel__list">
      {items.map((item, i) => (
        <li key={i} className="panel__list-item">
          <span>{item.label}</span>
          <span style={{ color: item.error ? "#ff5f56" : (item.change_pct ?? 0) >= 0 ? "#34c759" : "#ff5f56" }}>
            {item.error
              ? "unavailable"
              : `$${item.price?.toFixed(2) ?? "?"} (${item.change_pct?.toFixed(2) ?? "?"}%)`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ReportBody({ report }: { report: Report }) {
  switch (report.kind) {
    case "metrics":
      return <MetricsReport payload={report.payload} />;
    case "news":
      return <NewsReport payload={report.payload} />;
    case "stocks":
      return <StocksReport payload={report.payload} />;
    default:
      return <pre className="panel__value panel__mono">{JSON.stringify(report.payload, null, 2)}</pre>;
  }
}

// Schedule settings aren't backed by the API yet: that whole block is grayed out.
const DAYS = ["M", "T", "W", "T", "F", "S", "S"];
const SECTIONS = [
  { label: "Computer health", detail: "CPU, memory, disk, battery and pending updates", chips: [], on: true },
  { label: "Calendar", detail: "Today's meetings and free time", chips: [], on: true },
  { label: "Emails worth a look", detail: "Unread and important, summarised", chips: ["Work", "Billing"], on: true },
  { label: "Watchlist", detail: "Prices and a one-line take", chips: ["AAPL", "NVDA", "MSFT", "+"], on: true },
  { label: "Headlines", detail: "Sources you choose", chips: ["Tech", "Markets", "Local"], on: true },
  { label: "Weather", detail: "Only if it affects your day", chips: [], on: false },
];

function BriefingPanel() {
  const reports = useReportsSocket();

  return (
    <div className="brief">
      <div className="dash__head brief__head">
        <div>
          <p>What goes into your daily briefing, and when.</p>
        </div>
        <span className="dash__sub">schedule settings not available yet</span>
      </div>

      <div className="is-off" title="Not available yet">
        <div className="dash__grid4 brief__three">
          <div className="dash__card">
            <span className="brief__label">Time</span>
            <div className="brief__time">08:00 AM</div>
          </div>
          <div className="dash__card">
            <span className="brief__label">Days</span>
            <div className="brief__seg">
              {DAYS.map((d, i) => (
                <span key={i} className={i < 5 ? "brief__seg--on" : undefined}>{d}</span>
              ))}
            </div>
          </div>
          <div className="dash__card">
            <span className="brief__label">Deliver as</span>
            <div className="brief__seg brief__seg--wide">
              <span className="brief__seg--on">Sandbox</span>
              <span>Voice</span>
              <span>Both</span>
            </div>
          </div>
        </div>
        <div className="brief__bar">
          <span className="brief__label">Sections · in this order</span>
          <span>Run briefing now ↗</span>
        </div>
        {SECTIONS.map((sec) => (
          <div className="brief__row" key={sec.label}>
            <span className="brief__grip">⋮⋮</span>
            <div className="brief__text">
              <b>{sec.label}</b>
              <span>{sec.detail}</span>
            </div>
            <div className="brief__chips">
              {sec.chips.map((c) => (
                <span key={c}>{c}</span>
              ))}
            </div>
            <span className={`brief__toggle${sec.on ? " brief__toggle--on" : ""}`}><i /></span>
          </div>
        ))}
      </div>

      <div className="brief__bar">
        <span className="brief__label">Latest reports</span>
      </div>
      <div className="brief__reports">
        {reports.length === 0 && (
          <p className="panel__empty">No reports yet — daily briefing and report windows will show up here as they run.</p>
        )}
        {reports.map((report) => (
          <div key={report.id} className="panel__field">
            <span className="panel__section-heading">{report.title}</span>
            <ReportBody report={report} />
          </div>
        ))}
      </div>
    </div>
  );
}

export default BriefingPanel;
