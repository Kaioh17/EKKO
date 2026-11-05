import "./Panels.css";
import "./ConnectionsPanel.css";
import { useState } from "react";
import { ArrowLeft, ExternalLink, Lock, Pencil } from "lucide-react";
import { openUrl } from "@tauri-apps/plugin-opener";
import ProviderLogo from "../ProviderLogo";
import { GROUPS, SERVICES, type Field, type Icon, type Service } from "./connectionsData";

function Mark({ icon, size = 24 }: { icon: Icon; size?: number }) {
  if (icon === "microsoft") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
        <path fill="#f25022" d="M2 2h9.5v9.5H2z" /><path fill="#7fba00" d="M12.5 2H22v9.5h-9.5z" />
        <path fill="#00a4ef" d="M2 12.5h9.5V22H2z" /><path fill="#ffb900" d="M12.5 12.5H22V22h-9.5z" />
      </svg>
    );
  }
  if ("logo" in icon) return <ProviderLogo name={icon.logo} size={size} />;
  if ("path" in icon) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill={`#${icon.hex}`} role="img" aria-label={icon.title}>
        <path d={icon.path} />
      </svg>
    );
  }
  const Lucide = icon;
  return <Lucide size={size} aria-hidden />;
}

function FieldRow({ f }: { f: Field }) {
  if (f.kind === "toggle") {
    return (
      <label className="panel__toggle">
        <input type="checkbox" className="panel__toggle-input" defaultChecked={f.on} />
        <span className="panel__toggle-track" />
        {f.label}
      </label>
    );
  }
  return (
    <label className="panel__field">
      <span className="panel__label">{f.label}</span>
      {f.kind === "select" ? (
        <select className="panel__input" defaultValue={f.value}>
          {f.options.map((o) => <option key={o}>{o}</option>)}
        </select>
      ) : (
        <input
          className={`panel__input${f.mono ? " panel__mono" : ""}`}
          type={f.kind === "secret" ? "password" : "text"}
          autoComplete="off"
          defaultValue={f.value}
          placeholder={f.placeholder}
        />
      )}
      {f.hint && <span className="panel__label">{f.hint}</span>}
    </label>
  );
}

function Detail({ s, connected, onToggle, onBack }: { s: Service; connected: boolean; onToggle: () => void; onBack: () => void }) {
  return (
    <div className="panel panel--wide conn">
      <button type="button" className="conn__back" onClick={onBack}>
        <ArrowLeft size={14} /> All connections
      </button>

      <header className="conn__hero">
        <span className="conn__logo conn__logo--lg"><Mark icon={s.icon} size={32} /></span>
        <div className="conn__hero-text">
          <h2>{s.name}</h2>
          <span className="panel__label">{s.group} · {s.blurb}</span>
        </div>
        {s.soon ? (
          <span className="conn__badge">Coming soon</span>
        ) : (
          <div className="conn__hero-actions">
            <span className={`conn__badge${connected ? " conn__badge--on" : ""}`}>{connected ? "Connected" : "Not connected"}</span>
            <button type="button" className={`panel__button${connected ? "" : " panel__button--primary"}`} onClick={onToggle}>
              {connected ? "Disconnect" : s.connect}
            </button>
          </div>
        )}
      </header>

      <section className="conn__section">
        <span className="panel__section-heading">What ekko learns</span>
        <ul className="conn__learns">{s.learns.map((l) => <li key={l}>{l}</li>)}</ul>
        <p className="conn__privacy"><Lock size={12} /> Tokens and synced data stay on this computer. Disconnecting deletes both.</p>
      </section>

      {s.scopes && (
        <section className="conn__section">
          <span className="panel__section-heading">Permissions</span>
          <div className="panel__toggle-grid">
            {s.scopes.map((sc) => (
              <label key={sc.label} className="panel__toggle">
                <input type="checkbox" className="panel__toggle-input" defaultChecked={sc.on} />
                <span className="panel__toggle-track" />
                {sc.label}
                {sc.write && <span className="conn__write"><Pencil size={10} /> changes things</span>}
              </label>
            ))}
          </div>
        </section>
      )}

      {s.setup && (
        <section className="conn__section">
          <span className="panel__section-heading">Setup</span>
          <ol className="conn__steps">{s.setup.map((t) => <li key={t}>{t}</li>)}</ol>
        </section>
      )}

      {s.snippet && (
        <section className="conn__section">
          <span className="panel__section-heading">{s.group === "MCP servers" ? "Run this" : "Check it works"}</span>
          <pre className="conn__code">{s.snippet}</pre>
        </section>
      )}

      {s.fields && (
        <section className="conn__section conn__fields">
          <span className="panel__section-heading">Settings</span>
          {s.fields.map((f) => <FieldRow key={f.label} f={f} />)}
        </section>
      )}

      {s.docs && (
        <a
          className="conn__link"
          href={s.docs.href}
          onClick={(e) => {
            e.preventDefault();
            void openUrl(s.docs!.href);
          }}
        >
          {s.docs.label} <ExternalLink size={12} />
        </a>
      )}
    </div>
  );
}

function ConnectionsPanel() {
  const [group, setGroup] = useState<(typeof GROUPS)[number] | "All">("All");
  const [openId, setOpenId] = useState<string | null>(null);
  // Mock state only: which cards the user has "connected" this session.
  const [connected, setConnected] = useState<Set<string>>(new Set(["browser"]));

  const open = SERVICES.find((s) => s.id === openId);
  if (open) {
    const toggle = () =>
      setConnected((c) => {
        const n = new Set(c);
        if (!n.delete(open.id)) n.add(open.id);
        return n;
      });
    return <Detail s={open} connected={connected.has(open.id)} onToggle={toggle} onBack={() => setOpenId(null)} />;
  }

  const shown = group === "All" ? SERVICES : SERVICES.filter((s) => s.group === group);
  return (
    <div className="panel panel--wide conn">
      <p className="models__intro">
        The more ekko can see of your day, the less you have to explain. Connect only what you want; each one can be switched off on its own.
      </p>
      <div className="panel__tabs conn__filters">
        {(["All", ...GROUPS] as const).map((g) => (
          <button key={g} type="button" className={`panel__tab${g === group ? " panel__tab--active" : ""}`} onClick={() => setGroup(g)}>
            {g}
            <span className="panel__tab-count">{g === "All" ? SERVICES.length : SERVICES.filter((s) => s.group === g).length}</span>
          </button>
        ))}
      </div>
      <ul className="conn__grid">
        {shown.map((s) => {
          const on = connected.has(s.id);
          return (
            <li key={s.id}>
              <button type="button" className={`conn__card${on ? " conn__card--on" : ""}${s.soon ? " conn__card--soon" : ""}`} onClick={() => setOpenId(s.id)}>
                <span className="conn__logo"><Mark icon={s.icon} /></span>
                <span className="conn__name">{s.name}</span>
                <span className="conn__blurb">{s.blurb}</span>
                <span className={`conn__badge${on ? " conn__badge--on" : ""}`}>{s.soon ? "Soon" : on ? "Connected" : s.group}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default ConnectionsPanel;
