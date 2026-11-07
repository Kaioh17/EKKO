import "./Panels.css";
import "./ModelsPanel.css";
import { useEffect, useState } from "react";
import {
  getKeys,
  getProviders,
  type KeyStatus,
  type LlmSettings,
  type Provider,
  type ProviderInfo,
} from "../../api/client";
import { useSettingsForm } from "../../hooks/useSettingsForm";
import ProviderCard from "./ProviderCard";
import KeyRow from "./KeyRow";
import { DraftInput } from "./SettingsControls";
import { SettingsPage } from "./SettingsKit";

function ModelsPanel() {
  const llm = useSettingsForm<LlmSettings>("llm");
  const [keys, setKeys] = useState<KeyStatus | null>(null);
  const [keysLoaded, setKeysLoaded] = useState(false);
  const loadKeys = () =>
    getKeys()
      .then(setKeys)
      .catch(() => setKeys(null))
      .finally(() => setKeysLoaded(true));
  useEffect(() => void loadKeys(), []);
  // What each provider is (label, badge, key, model field) is the backend's llm_fallback/catalog.py.
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [providersError, setProvidersError] = useState(false);
  useEffect(() => {
    getProviders().then(setProviders).catch(() => setProvidersError(true));
  }, []);

  const s = llm.draft;
  // Wait for everything before first paint so the page doesn't grow/jump as pieces arrive.
  if (llm.loading || !keysLoaded || (!providers && !providersError)) {
    return (
      <div className="panel panel--wide">
        <p className="panel__empty">Loading…</p>
      </div>
    );
  }
  if (!s || !providers) {
    return (
      <div className="panel panel--wide">
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
    <SettingsPage forms={[llm]} note="Saving applies them to the next request." className="models">
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

      <div className="models__row">
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
      </div>

      <div className="panel__subsection">
        <span className="panel__section-heading">API keys</span>
        <span className="panel__label">Stored only on this computer. Keys are never shown again after saving.</span>
        {providers.flatMap((p) => (p.key_env ? [p.key_env] : [])).map((name) => (
          <KeyRow key={name} name={name} status={keys?.[name]} onChange={loadKeys} />
        ))}
      </div>
    </SettingsPage>
  );
}

export default ModelsPanel;
