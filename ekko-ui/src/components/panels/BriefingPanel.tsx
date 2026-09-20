import "./Panels.css";
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
          <a href={item.url} target="_blank" rel="noreferrer">
            {item.title}
          </a>
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

function BriefingPanel() {
  const reports = useReportsSocket();

  return (
    <div className="panel">
      <h2>Briefing</h2>
      {reports.length === 0 && (
        <p className="panel__empty">
          No reports yet — daily briefing and report windows will show up here as they run.
        </p>
      )}
      {reports.map((report) => (
        <div key={report.id} className="panel__field">
          <span className="panel__section-heading">{report.title}</span>
          <ReportBody report={report} />
        </div>
      ))}
    </div>
  );
}

export default BriefingPanel;
