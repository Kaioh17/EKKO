import "./Panels.css";
import "./Dashboard.css";
import "./BriefingPanel.css";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowRight, BatteryMedium, Check, Cpu, FileText, GripVertical, HardDrive, MemoryStick, Monitor, MonitorSpeaker,
  Plus, SlidersHorizontal, Sparkles, Volume2, Wifi, X, type LucideIcon,
} from "lucide-react";
import {
  previewBriefing,
  type BriefingMetric,
  type BriefingPreview,
  type BriefingSectionId,
  type BriefingSettings,
} from "../../api/client";
import { safeUrl } from "../../api/safeUrl";
import { useReportsSocket, type Report } from "../../hooks/useReportsSocket";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import { DraftInput, ToggleSwitch } from "./SettingsControls";

// ---------------------------------------------------------------- report cards
// Shared by the live "Latest reports" below and by the Preview result.

interface Metric {
  name: string;
  detail: string;
  flagged?: boolean;
}

interface NewsItem {
  id?: string;
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
  const narration = (payload.narration as Record<string, string> | undefined) ?? {};
  if (!items.length) return <p className="panel__empty">No news fetched.</p>;
  return (
    <ul className="panel__list">
      {items.map((item, i) => (
        <li key={i} className="panel__list-item brief__item">
          <span>
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
              item.title
            )}
            {item.id && narration[item.id] && <small className="brief__gloss">{narration[item.id]}</small>}
          </span>
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
        <li key={i} className="panel__list-item brief__item">
          <span>
            {item.label}
            {item.take && <small className="brief__gloss">{item.take}</small>}
          </span>
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

// The closing card of a spoken/on-screen run: plain, or how many tokens the AI narration used.
function RunReport({ payload }: { payload: Record<string, unknown> }) {
  return <p className="panel__value">{String(payload.note ?? "")}</p>;
}

function ReportBody({ kind, payload }: { kind: string; payload: Record<string, unknown> }) {
  switch (kind) {
    case "metrics":
      return <MetricsReport payload={payload} />;
    case "news":
      return <NewsReport payload={payload} />;
    case "stocks":
      return <StocksReport payload={payload} />;
    case "run":
      return <RunReport payload={payload} />;
    default:
      return <pre className="panel__value panel__mono">{JSON.stringify(payload, null, 2)}</pre>;
  }
}

function ReportCard({ report }: { report: Report }) {
  return (
    <div className="panel__field">
      <span className="panel__section-heading">{report.title}</span>
      <ReportBody kind={report.kind} payload={report.payload} />
    </div>
  );
}

// ---------------------------------------------------------------- form helpers

export const split = (raw: string) => [...new Set(raw.split(/[,\n]/).map((s) => s.trim()).filter(Boolean))];
export const lines = (raw: string) => [...new Set(raw.split("\n").map((s) => s.trim()).filter(Boolean))];
export const namesToText = (names: Record<string, string>) =>
  Object.entries(names).map(([k, v]) => `${k} = ${v}`).join("\n");
export const textToNames = (raw: string) =>
  Object.fromEntries(
    lines(raw)
      .map((l) => l.split("=").map((p) => p.trim()))
      .filter(([k, v]) => k && v),
  );

export function Num({ value, min, max, int = true, onCommit }: { value: number; min: number; max: number; int?: boolean; onCommit: (n: number) => void }) {
  return (
    <DraftInput
      type="number"
      min={min}
      max={max}
      value={String(value)}
      onCommit={(raw) => {
        const n = Number(raw);
        if (raw.trim() !== "" && Number.isFinite(n) && (!int || Number.isInteger(n)) && n >= min && n <= max) onCommit(n);
      }}
    />
  );
}

// Holds keystrokes locally and commits on blur, like DraftInput but multi-line.
export function Area({ value, onCommit, rows = 3, placeholder }: { value: string; onCommit: (raw: string) => void; rows?: number; placeholder?: string }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <textarea
      className="panel__input brief__area"
      rows={rows}
      value={draft}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onCommit(draft)}
    />
  );
}

export const SECTION_INFO: Record<BriefingSectionId, { label: string; detail: string }> = {
  system: { label: "Computer health", detail: "CPU, memory, disk, battery, GPU and network" },
  news: { label: "Tech news", detail: "Hacker News stories for your topics, ranked by points" },
  stocks: { label: "Watchlist", detail: "Prices, daily change and a short take on each ticker" },
};

export const METRICS: { id: BriefingMetric; label: string }[] = [
  { id: "cpu", label: "CPU" },
  { id: "ram", label: "Memory" },
  { id: "disk", label: "Disk" },
  { id: "battery", label: "Battery" },
  { id: "gpu", label: "GPU" },
  { id: "network", label: "Network" },
];

const usageText = (u: BriefingPreview["usage"]) =>
  u
    ? `${u.approx ? "About " : "~"}${(u.input + u.output).toLocaleString()} tokens (${u.input.toLocaleString()} in, ${u.output.toLocaleString()} out) across ${u.calls} call${u.calls === 1 ? "" : "s"} via ${u.models.join(", ")}`
    : "Plain: no AI used, 0 tokens.";

// ---------------------------------------------------------------- everyday page

export type AdvTab = "health" | "news" | "watchlist" | "narration" | "wording";
/** Where a deep link lands in Settings > Briefing; `n` makes a repeat click re-trigger the scroll + flash. */
export interface BriefingFocus { tab: AdvTab; row?: string; n: number }
export type OpenAdvanced = (to: Omit<BriefingFocus, "n">) => void;

const SECTION_LOOK: Record<BriefingSectionId, { to: Omit<BriefingFocus, "n">; link: string }> = {
  system: { to: { tab: "health", row: "limits" }, link: "Warning limits" },
  news: { to: { tab: "news", row: "window" }, link: "News options" },
  stocks: { to: { tab: "watchlist", row: "instructions" }, link: "Watchlist instructions" },
};
const METRIC_ICON: Record<BriefingMetric, LucideIcon> = { cpu: Cpu, ram: MemoryStick, disk: HardDrive, battery: BatteryMedium, gpu: Monitor, network: Wifi };

function Chip({ on, Icon, onClick, children }: { on: boolean; Icon: LucideIcon; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-pressed={on} className={`bf-chip${on ? " is-on" : ""}`} onClick={onClick}>
      <Icon size={18} />
      <span>{children}</span>
      {on && <Check size={14} className="bf-chip__tick" />}
    </button>
  );
}

// Tags with a ✕. "+ Add" turns into an underlined box; Enter or a comma turns the text into a tag.
function TagInput({ tags, onChange, noun, upper = false, children }: { tags: string[]; onChange: (t: string[]) => void; noun: string; upper?: boolean; children?: ReactNode }) {
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const add = (raw: string[]) => {
    const next = [...tags];
    for (const r of raw.map((x) => x.trim()).filter(Boolean)) {
      const v = upper ? r.toUpperCase() : r;
      if (!next.includes(v)) next.push(v);
    }
    if (next.length !== tags.length) onChange(next);
  };
  return (
    <div className="bf-tags">
      {tags.map((t) => (
        <span className="bf-tag" key={t}>
          {t}
          <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(tags.filter((x) => x !== t))}>
            <X size={13} />
          </button>
        </span>
      ))}
      {adding ? (
        <input
          className="bf-tags__input"
          autoFocus
          value={text}
          placeholder={`${noun}, then Enter`}
          aria-label={`Add ${noun}`}
          onChange={(e) => {
            const parts = e.target.value.split(",");
            if (parts.length > 1) add(parts.slice(0, -1));
            setText(parts[parts.length - 1]);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (add([text]), setText(""));
            if (e.key === "Escape") (setText(""), setAdding(false));
          }}
          onBlur={() => (add([text]), setText(""), setAdding(false))}
        />
      ) : (
        <button type="button" className="bf-tags__add" onClick={() => setAdding(true)}>
          <Plus size={14} /> Add {noun}
        </button>
      )}
      {children && <span className="bf-tags__extra">{children}</span>}
    </div>
  );
}

function Preview({ saving }: { saving: boolean }) {
  const [result, setResult] = useState<BriefingPreview | null>(null);
  const [busy, setBusy] = useState<"plain" | "llm" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = (mode: "plain" | "llm") => {
    setBusy(mode);
    setError(null);
    previewBriefing(mode)
      .then(setResult)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(null));
  };

  return (
    <div className="dash__card dash__card--wide">
      <div className="dash__title-row"><h3 className="dash__title">Try it now</h3><span className="dash__sub">nothing is spoken</span></div>
      <span className="dash__kicker">Run the briefing to see what it would show. No tabs open.</span>
      <div className="bf-actions">
        <button type="button" className="panel__button" disabled={busy !== null} onClick={() => run("plain")}>
          {busy === "plain" ? "Running…" : "Preview as plain"}
        </button>
        <button type="button" className="panel__button" disabled={busy !== null} onClick={() => run("llm")}>
          {busy === "llm" ? "Running…" : "Preview with AI"}
        </button>
      </div>
      {saving && <span className="dash__sub">Your latest edit is still saving.</span>}
      {error && <span className="panel__warning">{error}</span>}
      {result && (
        <div className="brief__reports bf-reports">
          {result.sections.length === 0 && <p className="panel__empty">Nothing to show: every section is off.</p>}
          {result.sections.map((sec) => (
            <div key={sec.id} className="panel__field">
              <span className="panel__section-heading">{SECTION_INFO[sec.id].label}</span>
              <ReportBody kind={sec.kind} payload={sec.payload} />
              {sec.spoken && <p className="panel__empty brief__spoken">Would say: “{sec.spoken}”</p>}
            </div>
          ))}
          <p className="panel__value brief__tokens">
            {result.mode === "llm" ? "AI narration. " : "Plain. "}
            {usageText(result.usage)} · {result.seconds}s
          </p>
        </div>
      )}
    </div>
  );
}

// Saves itself shortly after each edit, and on leaving the page, so there is no save bar to think about.
function useAutosave(form: ReturnType<typeof useSettingsForm<BriefingSettings>>) {
  const save = useRef(form.save);
  const dirty = useRef(form.dirty);
  save.current = form.save;
  dirty.current = form.dirty;
  useEffect(() => {
    if (!form.dirty) return;
    const t = setTimeout(() => void save.current(), 500);
    return () => clearTimeout(t);
  }, [form.draft, form.dirty]);
  useEffect(() => () => void (dirty.current && save.current()), []);
}

function BriefingPanel({ onAdvanced }: { onAdvanced: OpenAdvanced }) {
  const form = useSettingsForm<BriefingSettings>("briefing");
  const reports = useReportsSocket();
  useAutosave(form);
  const cards = useRef<Record<string, HTMLElement | null>>({});
  const [dragging, setDragging] = useState<BriefingSectionId | null>(null);
  const [soon, setSoon] = useState(false);
  const s = form.draft;

  const sections = s?.sections;
  const move = (id: BriefingSectionId, to: number) => {
    if (!sections) return;
    const from = sections.findIndex((x) => x.id === id);
    if (from === to || to < 0 || to >= sections.length) return;
    const next = [...sections];
    next.splice(to, 0, ...next.splice(from, 1));
    form.set({ sections: next });
  };

  // Pointer-driven drag (HTML5 drag-and-drop is swallowed by Tauri's file-drop handler on Windows).
  // A card swaps once the pointer passes its midpoint, which keeps tall and short cards from flip-flopping.
  const moveRef = useRef(move);
  moveRef.current = move;
  const orderRef = useRef(sections);
  orderRef.current = sections;
  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      const order = orderRef.current ?? [];
      const from = order.findIndex((x) => x.id === dragging);
      let to = from;
      order.forEach((x, j) => {
        const r = cards.current[x.id]?.getBoundingClientRect();
        if (!r) return;
        const mid = r.top + r.height / 2;
        if (j > from && e.clientY > mid) to = Math.max(to, j);
        if (j < from && e.clientY < mid) to = Math.min(to, j);
      });
      moveRef.current(dragging, to);
    };
    const stop = () => setDragging(null);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragging]);

  if (form.loading || !s) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">{form.loading ? "Loading…" : (form.error ?? "Settings unavailable.")}</p>
      </div>
    );
  }

  const toggleMetric = (id: BriefingMetric, on: boolean) =>
    form.set({ system_metrics: METRICS.map((m) => m.id).filter((m) => (m === id ? on : s.system_metrics.includes(m))) });

  const body = (id: BriefingSectionId) => {
    switch (id) {
      case "system":
        return (
          <div className="bf-chips">
            {METRICS.map((m) => (
              <Chip key={m.id} Icon={METRIC_ICON[m.id]} on={s.system_metrics.includes(m.id)} onClick={() => toggleMetric(m.id, !s.system_metrics.includes(m.id))}>
                {m.label}
              </Chip>
            ))}
          </div>
        );
      case "news":
        return (
          <TagInput tags={s.news_topics} noun="topic" onChange={(news_topics) => form.set({ news_topics })}>
            <ToggleSwitch label="Open stories in tabs" checked={s.open_tabs} onChange={(open_tabs) => form.set({ open_tabs })} />
          </TagInput>
        );
      case "stocks":
        return <TagInput tags={s.tickers} noun="ticker" upper onChange={(tickers) => form.set({ tickers })} />;
    }
  };

  const saving = form.busy || form.dirty;
  const status = form.error ?? (saving ? "Saving…" : "All changes saved");

  return (
    <div className="dash brief">
      <div className="dash__head bf-head">
        <div>
          <h2>Your morning, your way.</h2>
          <p>Choose what ekko tells you, in the order you like. Changes save as you go and apply to your next briefing.</p>
        </div>
        <div className="bf-head__side">
          <span className="dash__sub" role="status">{status}</span>
          <button type="button" className="panel__button bf-btn" onClick={() => onAdvanced({ tab: "narration" })}>
            <SlidersHorizontal size={14} /> Advanced settings
          </button>
        </div>
      </div>

      <div className="bf">
        <article className="bf__card">
          <div className="bf__gutter" />
          <div className="bf__main">
            <header className="bf__head">
                            <div className="bf__title"><h3>Narration</h3><p>How your briefing is told and where it shows up</p></div>
            </header>
            <div className="bf__body bf__body--two">
              <div className="bf__group">
                <span className="bf__label">How it's told</span>
                <div className="bf-chips">
                  <Chip Icon={FileText} on={s.mode === "plain"} onClick={() => form.set({ mode: "plain" })}>Plain</Chip>
                  <Chip Icon={Sparkles} on={s.mode === "llm"} onClick={() => form.set({ mode: "llm" })}>AI narration</Chip>
                </div>
                {s.mode === "plain" && (
                  <p className="bf__warn" role="alert">Plain mode uses no AI and ekko will not speak: your briefing only appears on screen.</p>
                )}
              </div>
              {s.mode === "llm" && (
                <div className="bf__group">
                  <span className="bf__label">How long it runs</span>
                  <div className="bf-chips">
                    {(["short", "regular", "long"] as const).map((len) => (
                      <Chip key={len} Icon={FileText} on={s.narration_length === len} onClick={() => form.set({ narration_length: len })}>
                        {len[0].toUpperCase() + len.slice(1)}
                      </Chip>
                    ))}
                  </div>
                </div>
              )}
              <div className="bf__group">
                <span className="bf__label">Deliver as</span>
                <div className="bf-chips">
                  <Chip Icon={Monitor} on={s.deliver === "sandbox"} onClick={() => form.set({ deliver: "sandbox" })}>On screen</Chip>
                  <Chip Icon={Volume2} on={s.deliver === "voice"} onClick={() => form.set({ deliver: "voice" })}>Voice</Chip>
                  <Chip Icon={MonitorSpeaker} on={s.deliver === "both"} onClick={() => form.set({ deliver: "both" })}>Both</Chip>
                </div>
              </div>
              <button type="button" className="bf__link" onClick={() => onAdvanced({ tab: "narration", row: "provider" })}>
                AI provider and wording <ArrowRight size={14} />
              </button>
            </div>
          </div>
        </article>

        {s.sections.map((sec, i) => {
          const { to, link } = SECTION_LOOK[sec.id];
          const info = SECTION_INFO[sec.id];
          return (
            <article
              key={sec.id}
              ref={(el) => void (cards.current[sec.id] = el)}
              className={`bf__card${dragging === sec.id ? " is-dragging" : ""}${sec.enabled ? "" : " bf__card--off"}`}
            >
              <button
                type="button"
                className="bf__gutter bf__grip"
                aria-label={`Reorder ${info.label}. Drag, or use the up and down arrow keys.`}
                onPointerDown={(e) => (e.preventDefault(), setDragging(sec.id))}
                onKeyDown={(e) => {
                  if (e.key === "ArrowUp") (e.preventDefault(), move(sec.id, i - 1));
                  if (e.key === "ArrowDown") (e.preventDefault(), move(sec.id, i + 1));
                }}
              >
                <GripVertical size={18} />
              </button>
              <div className="bf__main">
                <header className="bf__head">
                                    <div className="bf__title"><h3>{info.label}</h3><p>{info.detail}</p></div>
                  <ToggleSwitch
                    label=""
                    checked={sec.enabled}
                    onChange={(enabled) => form.set({ sections: s.sections.map((x) => (x.id === sec.id ? { ...x, enabled } : x)) })}
                  />
                </header>
                <div className="bf__body">
                  {body(sec.id)}
                  <button type="button" className="bf__link" onClick={() => onAdvanced(to)}>
                    {link} <ArrowRight size={14} />
                  </button>
                </div>
              </div>
            </article>
          );
        })}

        <div className="bf__add">
          <button type="button" className="bf__add-btn" onClick={() => setSoon(true)}>
            <Plus size={18} />
            <span><b>Add to your briefing</b><small>{soon ? "Not available yet. Calendar, weather and mail are planned." : "Calendar, weather, mail…"}</small></span>
          </button>
        </div>
      </div>

      <div className="dash__split dash__split--even">
        <Preview saving={saving} />
        <div className="dash__card dash__card--wide">
          <div className="dash__title-row"><h3 className="dash__title">Latest reports</h3><span className="dash__sub">with what each cost</span></div>
          <div className="brief__reports bf-reports">
            {reports.length === 0 && <p className="panel__empty">No reports yet. Ask ekko for your briefing and it will appear here.</p>}
            {reports.map((report) => (
              <ReportCard key={report.id} report={report} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default BriefingPanel;
