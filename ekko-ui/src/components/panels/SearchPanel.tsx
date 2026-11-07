import "./Panels.css";
import "./ModelsPanel.css";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import {
  getKeys,
  getSearchProviders,
  getSearchUsage,
  testSearchProvider,
  type KeyStatus,
  type LlmSettings,
  type SearchProvider,
  type SearchProviderInfo,
  type SearchSettings,
  type SearchUsage,
} from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import KeyRow from "./KeyRow";
import { DraftInput } from "./SettingsControls";
import { SettingsPage } from "./SettingsKit";

function Link({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} onClick={(e) => { e.preventDefault(); void openUrl(href); }}>
      {children}
    </a>
  );
}

function UsageLine({ info, usage }: { info: SearchProviderInfo; usage: SearchUsage | undefined }) {
  if (!usage) return null;
  const { live } = usage;
  const parts = [`${usage.searches_this_month} searches from EKKO this month`];
  if (live?.used != null) parts.push(`${live.used}${live.limit != null ? ` of ${live.limit}` : ""} credits used${live.plan ? ` (${live.plan})` : ""}`);
  else if (usage.remaining != null) parts.push(`${usage.remaining} left this month, per ${info.label}`);
  if (usage.last_ms != null) parts.push(`last search ${usage.last_ms} ms`);
  return (
    <>
      <span className="models__detail">{parts.join(" · ")}</span>
      {usage.last_error && <span className="panel__warning">Last attempt failed: {usage.last_error}</span>}
      {!live && usage.remaining == null && (
        <span className="models__detail">{info.label} doesn't report exact usage to EKKO. The dashboard below has the real numbers.</span>
      )}
    </>
  );
}

function ProviderCard({ info, selected, keys, usage, onSelect, onChanged }: {
  info: SearchProviderInfo;
  selected: boolean;
  keys: KeyStatus | null;
  usage: SearchUsage | undefined;
  onSelect: () => void;
  onChanged: () => void;
}) {
  const hasKey = !!keys?.[info.key_env]?.set;
  const [test, setTest] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const runTest = () => {
    setTesting(true);
    setTest(null);
    testSearchProvider(info.name)
      .then((r) => setTest(`Works: ${r.results} results in ${r.elapsed_ms} ms.`))
      .catch((e: Error) => setTest(`Failed: ${e.message}`))
      .finally(() => {
        setTesting(false);
        onChanged();
      });
  };
  return (
    <li className={`models__card models__card--norank${selected ? " models__card--active" : ""}`}>
      <div className="models__body">
        <div className="models__head">
          <span className="models__name">{info.label}</span>
          <span className="models__model">{info.kind}</span>
        </div>
        <span className="models__detail">{info.detail}</span>
        <span className="models__detail"><b>Cost:</b> {info.cost}</span>
        <span className="models__detail"><b>Good:</b> {info.pros}</span>
        <span className="models__detail"><b>Watch out:</b> {info.cons}</span>

        <div className="panel__subsection">
          <span className="panel__label">How to get a key</span>
          <ol className="models__detail" style={{ margin: 0, paddingLeft: "1.2em" }}>
            {info.steps.map((s) => <li key={s}>{s}</li>)}
          </ol>
          <span className="models__detail">
            <Link href={info.key_url}>{`Open the ${info.label} key page`}</Link> · <Link href={info.dashboard_url}>Open usage dashboard</Link>
          </span>
        </div>

        <KeyRow name={info.key_env} status={keys?.[info.key_env]} onChange={onChanged} />
        {hasKey && <UsageLine info={info} usage={usage} />}
        {hasKey && (
          <div className="models__key-row">
            <button type="button" className="panel__button" disabled={testing} onClick={runTest}>
              {testing ? "Testing…" : "Test (uses 1 search)"}
            </button>
            {test && <span className="models__detail">{test}</span>}
          </div>
        )}
      </div>
      <div className="models__controls">
        <span className="models__badge">{selected ? "In use" : "Not in use"}</span>
        {!selected && (
          <button type="button" className="panel__button" onClick={onSelect}>
            Use this one
          </button>
        )}
      </div>
    </li>
  );
}

function SearchPanel() {
  const search = useSettingsForm<SearchSettings>("search");
  const llm = useSettingsForm<LlmSettings>("llm");
  const [providers, setProviders] = useState<SearchProviderInfo[] | null>(null);
  const [keys, setKeys] = useState<KeyStatus | null>(null);
  const [usage, setUsage] = useState<Record<SearchProvider, SearchUsage> | null>(null);
  const [failed, setFailed] = useState(false);
  const refresh = () => {
    getKeys().then(setKeys).catch(() => setKeys(null));
    getSearchUsage().then(setUsage).catch(() => setUsage(null));
  };
  useEffect(() => {
    getSearchProviders().then(setProviders).catch(() => setFailed(true));
    refresh();
  }, []);

  if (search.loading || llm.loading || (!providers && !failed)) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">Loading…</p>
      </div>
    );
  }
  const s = search.draft;
  const l = llm.draft;
  if (!s || !l || !providers) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">{search.error ?? "Settings unavailable."}</p>
      </div>
    );
  }
  const keyMissing = !keys?.[providers.find((p) => p.name === s.provider)?.key_env ?? ""]?.set;

  return (
    <SettingsPage forms={[search, llm]} note="Saving applies to the next request." className="models">
      <p className="models__intro">
        When you ask about news, prices, scores or anything that may have changed, EKKO searches the live web and the model answers from what it finds.
        Searches cost money or credits, so EKKO only searches when a question needs it. Pick one service below.
      </p>
      <ol className="models__list">
        {providers.map((p) => (
          <ProviderCard
            key={p.name}
            info={p}
            selected={s.provider === p.name}
            keys={keys}
            usage={usage?.[p.name]}
            onSelect={() => search.set({ provider: p.name })}
            onChanged={refresh}
          />
        ))}
      </ol>
      {l.research_enabled && keyMissing && (
        <span className="panel__warning">No key for the selected service yet, so live search is off. EKKO falls back to its older page scraper.</span>
      )}
      <div className="models__row">
        <div className="panel__toggle-grid">
          <label className="panel__toggle">
            <input type="checkbox" className="panel__toggle-input" checked={l.research_enabled} onChange={(e) => llm.set({ research_enabled: e.target.checked })} />
            <span className="panel__toggle-track" />
            Live web research for news questions
          </label>
        </div>
        <label className="panel__field models__narrow">
          <span className="panel__label">Results per search (1 to 10)</span>
          <DraftInput
            type="number"
            min={1}
            max={10}
            value={String(s.max_results)}
            onCommit={(raw) => {
              const n = Number(raw);
              if (raw.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= 10) search.set({ max_results: n });
            }}
          />
        </label>
      </div>
    </SettingsPage>
  );
}

export default SearchPanel;
