import "./Panels.css";
import "./ConnectionsPanel.css";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ExternalLink, Lock, Pencil } from "lucide-react";
import { openUrl } from "@tauri-apps/plugin-opener";
import ProviderLogo from "../ProviderLogo";
import google from "../../assets/google.png";
import {
  connectSpotify, disconnectSpotify, getSpotify, setSpotifyConfig, spotifyPrompt,
  type SpotifyRun, type SpotifyStatus,
} from "../../api/client";
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
  if (icon === "google") return <img src={google} width={size} height={size} alt="Google" />;
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


const STORE_LABEL: Record<string, string> = { file: "File in ekko's data folder", keyring: "OS keychain (Windows Credential Manager)" };

function SpotifySection({ st, refresh }: { st: SpotifyStatus | null; refresh: () => Promise<void> }) {
  const [clientId, setClientId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [run, setRun] = useState<SpotifyRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { if (st) setClientId(st.client_id); }, [st?.client_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await fn(); await refresh(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  if (!st) return <section className="conn__section"><span className="panel__label">{err || "Loading…"}</span></section>;

  return (
    <>
      <section className="conn__section conn__fields">
        <span className="panel__section-heading">Spotify app</span>
        <label className="panel__field">
          <span className="panel__label">Client ID</span>
          <input className="panel__input panel__mono" autoComplete="off" value={clientId} onChange={(e) => setClientId(e.target.value)}
            onBlur={() => clientId !== st.client_id && act(() => setSpotifyConfig({ client_id: clientId }))} placeholder="From developer.spotify.com/dashboard" />
          <span className="panel__label">Add this Redirect URI to your Spotify app: {st.redirect_uri}</span>
        </label>
        <label className="panel__field">
          <span className="panel__label">Keep the sign-in token in</span>
          <select className="panel__input" value={st.store} onChange={(e) => act(() => setSpotifyConfig({ store: e.target.value }))}>
            {st.stores.map((o) => <option key={o} value={o}>{STORE_LABEL[o] ?? o}</option>)}
          </select>
          <span className="panel__label">PKCE sign-in: no client secret exists. Only the refresh token is secret, and it lives only here.</span>
        </label>
        <div>
          {st.connected ? (
            <button type="button" className="panel__button" disabled={busy} onClick={() => act(disconnectSpotify)}>Disconnect</button>
          ) : (
            <button type="button" className="panel__button panel__button--primary" disabled={busy || !st.client_id}
              onClick={() => act(async () => { await openUrl((await connectSpotify()).url); })}>
              Connect Spotify
            </button>
          )}
          {!st.connected && <span className="panel__label"> Approve in the browser, then press Refresh status below.</span>}
        </div>
        {!st.connected && <button type="button" className="panel__button" onClick={() => act(async () => {})}>Refresh status</button>}
      </section>

      {st.connected && (
        <section className="conn__section conn__fields">
          <span className="panel__section-heading">Try a prompt</span>
          <form onSubmit={(e) => { e.preventDefault(); void act(async () => setRun(await spotifyPrompt(prompt))); }}>
            <input className="panel__input" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="play lonely at the top" disabled={busy} />
          </form>
          {busy && <span className="panel__label">Working…</span>}
          {run && (
            <pre className="conn__code">
              {run.calls.map((c) => `${c.tool}(${JSON.stringify(c.args)}) -> ${c.error ? "ERROR " : ""}${c.result}
`).join("")}
              {run.reply}
            </pre>
          )}
        </section>
      )}
      {err && <span className="panel__label">{err}</span>}
    </>
  );
}

function Detail({ s, connected, onToggle, onBack, spotify, refreshSpotify }: { s: Service; connected: boolean; onToggle: () => void; onBack: () => void; spotify: SpotifyStatus | null; refreshSpotify: () => Promise<void> }) {
  const live = s.id === "spotify";
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
        ) : live ? (
          <span className={`conn__badge${connected ? " conn__badge--on" : ""}`}>{connected ? "Connected" : "Not connected"}</span>
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

      {live && <SpotifySection st={spotify} refresh={refreshSpotify} />}

      {s.scopes && !live && (
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

      {s.fields && !live && (
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

  const [spotify, setSpotify] = useState<SpotifyStatus | null>(null);
  const refreshSpotify = useCallback(async () => setSpotify(await getSpotify()), []);
  useEffect(() => { refreshSpotify().catch(() => {}); }, [refreshSpotify]);
  const isOn = (id: string) => (id === "spotify" ? !!spotify?.connected : connected.has(id));

  const open = SERVICES.find((s) => s.id === openId);
  if (open) {
    const toggle = () =>
      setConnected((c) => {
        const n = new Set(c);
        if (!n.delete(open.id)) n.add(open.id);
        return n;
      });
    return <Detail s={open} connected={isOn(open.id)} onToggle={toggle} onBack={() => setOpenId(null)} spotify={spotify} refreshSpotify={refreshSpotify} />;
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
          const on = isOn(s.id);
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
