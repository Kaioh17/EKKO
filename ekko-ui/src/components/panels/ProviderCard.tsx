import { ArrowDown, ArrowUp } from "lucide-react";
import type { KeyStatus, LlmSettings, Provider, ProviderInfo } from "../../api/client";
import { DraftInput } from "./SettingsControls";

interface Props {
  info: ProviderInfo;
  /** 1-based place in the failover chain, or null when the provider is off. */
  rank: number | null;
  order: Provider[];
  settings: LlmSettings;
  keys: KeyStatus | null;
  onSet: (patch: Partial<LlmSettings>) => void;
  onOrder: (next: Provider[]) => void;
}

function ProviderCard({ info, rank, order, settings, keys, onSet, onOrder }: Props) {
  const p = info.name;
  const primary = order[0] === p;
  const model = info.model_setting ? settings[info.model_setting] : info.model;
  const move = (by: -1 | 1) => {
    const next = [...order];
    const i = order.indexOf(p);
    [next[i], next[i + by]] = [next[i + by], next[i]];
    onOrder(next);
  };
  return (
    <li className={`models__card${primary ? " models__card--active" : ""}${rank == null ? " models__card--off" : ""}`}>
      <span className="models__rank">{rank ?? "–"}</span>
      <div className="models__body">
        <div className="models__head">
          <span className="models__name">{info.label}</span>
          <span className="models__model">{model}</span>
        </div>
        <span className="models__detail">{info.detail}</span>
        {info.key_env && keys && !keys[info.key_env]?.set && <span className="panel__warning">No API key yet, add it below.</span>}
        {rank != null && (info.model_setting || info.paid) && (
          <div className="models__inline">
            {info.model_setting && (
              <label className="panel__field">
                <span className="panel__label">Model</span>
                <DraftInput
                  type="text"
                  value={model ?? ""}
                  onCommit={(raw) => raw.trim() && onSet({ [info.model_setting!]: raw.trim() })}
                />
              </label>
            )}
            {info.paid && (
              <label className="panel__field">
                <span className="panel__label">Spend cap (USD)</span>
                <DraftInput
                  type="number"
                  min={0}
                  step={0.5}
                  value={String(settings.budget_usd[p])}
                  onCommit={(raw) => {
                    const n = Number(raw);
                    if (raw.trim() !== "" && n >= 0) onSet({ budget_usd: { ...settings.budget_usd, [p]: n } });
                  }}
                />
              </label>
            )}
          </div>
        )}
      </div>
      <div className="models__controls">
        <span className="models__badge">{primary ? `Primary · ${info.kind}` : info.kind}</span>
        {rank != null && (
          <div className="models__buttons">
            <button type="button" className="panel__button models__icon" aria-label={`Move ${info.label} up`} disabled={rank === 1} onClick={() => move(-1)}>
              <ArrowUp size={14} />
            </button>
            <button type="button" className="panel__button models__icon" aria-label={`Move ${info.label} down`} disabled={rank === order.length} onClick={() => move(1)}>
              <ArrowDown size={14} />
            </button>
            {!primary && (
              <button type="button" className="panel__button" onClick={() => onOrder([p, ...order.filter((x) => x !== p)])}>
                Make primary
              </button>
            )}
          </div>
        )}
        <label className="panel__toggle">
          <input
            type="checkbox"
            className="panel__toggle-input"
            checked={rank != null}
            disabled={primary}
            title={primary ? "The primary provider is always in the chain" : undefined}
            onChange={(e) => onOrder(e.target.checked ? [...order, p] : order.filter((x) => x !== p))}
          />
          <span className="panel__toggle-track" />
          {rank != null ? "In chain" : "Off"}
        </label>
      </div>
    </li>
  );
}

export default ProviderCard;
