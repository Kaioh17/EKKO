import "./Panels.css";
import "./ModelsPanel.css";
import { useEffect, useState } from "react";
import {
  deleteKey,
  getKeys,
  getProviders,
  setKey,
  type KeyName,
  type KeyStatus,
  type LlmSettings,
  type Provider,
  type ProviderInfo,
} from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import ProviderCard from "./ProviderCard";
import { DraftInput, SettingsActions } from "./SettingsControls";
import VoiceSection from "./VoiceSection";

function KeyRow({ name, status, onChange }: { name: KeyName; status: KeyStatus[KeyName] | undefined; onChange: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = (call: () => Promise<void>) =>
    call()
      .then(() => {
        setValue("");
        setError(null);
        onChange();
      })
      .catch((e: Error) => setError(e.message));

  return (
    <div className="panel__field models__key">
      <span className="panel__label">
        {name} · {status?.set ? (status.last4 ? `set, ends ${status.last4}` : "set") : "not set"}
      </span>
      <div className="models__key-row">
        <input
          className="panel__input panel__mono"
          type="password"
          autoComplete="off"
          placeholder={status?.set ? "Paste a new key to replace" : "Paste key"}
          value={value}
          onChange={(e) => setValue(e.target.value.trim())}
        />
        <button type="button" className="panel__button" disabled={!value} onClick={() => run(() => setKey(name, value))}>
          Save
        </button>
        <button type="button" className="panel__button" disabled={!status?.set} onClick={() => run(() => deleteKey(name))}>
          Remove
        </button>
      </div>
      {error && <span className="panel__warning">{error}</span>}
    </div>
  );
}

function ModelsPanel() {
  const llm = useSettingsForm<LlmSettings>("llm");
  const [keys, setKeys] = useState<KeyStatus | null>(null);
  const loadKeys = () => getKeys().then(setKeys).catch(() => setKeys(null));
  useEffect(() => void loadKeys(), []);
  // What each provider is (label, badge, key, model field) is the backend's llm_fallback/catalog.py.
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [providersError, setProvidersError] = useState(false);
  useEffect(() => {
    getProviders().then(setProviders).catch(() => setProvidersError(true));
  }, []);

  const s = llm.draft;
  if (llm.loading || (!providers && !providersError)) {
    return (
      <div className="panel panel--wide">
        <h2>Models</h2>
        <p className="panel__empty">Loading…</p>
      </div>
    );
  }
  if (!s || !providers) {
    return (
      <div className="panel panel--wide">
        <h2>Models</h2>
        <p className="panel__empty">{llm.error ?? "Settings unavailable."}</p>
      </div>
    );
  }

  const order = s.failover_order;
  const infos = Object.fromEntries(providers.map((p) => [p.name, p]));
  const excluded = providers.map((p) => p.name).filter((p) => !order.includes(p));
  // The first provider in the chain is the primary: anything above the
  // primary would never be tried, so the two are kept identical here.
  const setOrder = (next: Provider[]) => llm.set({ failover_order: next, provider: next[0] });

  return (
    <div className="panel panel--wide">
      <h2>Models</h2>
      <p className="models__intro">
        When a request has no matching intent, the primary provider answers. If it fails, the next one in the chain takes over.
      </p>
      <ol className="models__list">
        {[...order, ...excluded].map((p, i) => (
          <ProviderCard
            key={p}
            info={infos[p]}
            rank={i < order.length ? i + 1 : null}
            order={order}
            settings={s}
            keys={keys}
            onSet={llm.set}
            onOrder={setOrder}
          />
        ))}
      </ol>

      <div className="panel__toggle-grid">
        <label className="panel__toggle">
          <input type="checkbox" className="panel__toggle-input" checked={s.failover_enabled} onChange={(e) => llm.set({ failover_enabled: e.target.checked })} />
          <span className="panel__toggle-track" />
          Fail over to the next provider
        </label>
        <label className="panel__toggle">
          <input type="checkbox" className="panel__toggle-input" checked={s.research_enabled} onChange={(e) => llm.set({ research_enabled: e.target.checked })} />
          <span className="panel__toggle-track" />
          Live web research for news questions
        </label>
      </div>
      <label className="panel__field models__narrow">
        <span className="panel__label">Skip a failed provider for (seconds)</span>
        <DraftInput
          type="number"
          min={0}
          value={String(s.failover_cooldown_s)}
          onCommit={(raw) => {
            const n = Number(raw);
            if (raw.trim() !== "" && n >= 0) llm.set({ failover_cooldown_s: n });
          }}
        />
      </label>
      <SettingsActions dirty={llm.dirty} busy={llm.busy} error={llm.error} onSave={llm.save} onReset={llm.reset} />

      <VoiceSection />

      <div className="panel__subsection">
        <span className="panel__section-heading">API keys</span>
        <span className="panel__label">Stored only on this computer. Keys are never shown again after saving.</span>
        {Object.keys(keys ?? {}).map((name) => (
          <KeyRow key={name} name={name} status={keys?.[name]} onChange={loadKeys} />
        ))}
      </div>
    </div>
  );
}

export default ModelsPanel;
