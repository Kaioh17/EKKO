import "./Panels.css";
import { useEffect, useState } from "react";
import { getDomains, getIntents, getThreshold, type Domain, type Intent } from "../../api/client";

function RoutingPanel() {
  const [intents, setIntents] = useState<Intent[]>([]);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getIntents(), getThreshold(), getDomains()])
      .then(([intentList, thresholdOut, domainList]) => {
        setIntents(intentList);
        setThreshold(thresholdOut.threshold);
        setDomains(domainList);
      })
      .catch(() => {
        setIntents([]);
        setThreshold(null);
        setDomains([]);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="panel">
        <h2>Routing</h2>
        <p className="panel__empty">Loading…</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Routing</h2>
      <div className="panel__field">
        <span className="panel__label">Match threshold</span>
        <span className="panel__value panel__mono">{threshold ?? "unavailable"}</span>
      </div>
      <div className="panel__field">
        <span className="panel__label">Intents ({intents.length})</span>
        <ul className="panel__list">
          {intents.map((intent) => (
            <li key={intent.key} className="panel__list-item">
              <span className="panel__mono">{intent.key}</span>
              <span className="panel__mono">{intent.handler}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="panel__field">
        <span className="panel__label">Domains ({domains.length})</span>
        <ul className="panel__list">
          {domains.map((domain) => (
            <li key={domain.key} className="panel__list-item">
              <span className="panel__mono">{domain.key}</span>
              <span className="panel__mono">threshold {domain.threshold}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default RoutingPanel;
