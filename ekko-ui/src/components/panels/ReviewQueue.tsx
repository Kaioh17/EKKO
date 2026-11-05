import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { getReview, resolveReview, type ReviewAction, type ReviewItem } from "../../api/client";
import ClassTag from "./ClassTag";

const FOLDER_KEY = "ekko.review.folder";
const BUILT_IN_FOLDER = "~/ekko-sandbox"; // what the backend uses when no folder is sent

// Native folder picker (works on Windows, macOS and Linux). null = cancelled.
async function pickFolder(defaultPath?: string) {
  const dir = await open({ directory: true, multiple: false, defaultPath: defaultPath || undefined });
  return typeof dir === "string" ? dir : null;
}

function leavesIn(iso: string) {
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (mins <= 0) return "leaving the log now";
  return mins < 60 ? `leaves the log in ${mins}m` : `leaves the log in ${Math.round(mins / 60)}h`;
}

// Keep-class exchanges that will be lost when they leave the 24h log.
function ReviewQueue() {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [folder, setFolder] = useState(() => localStorage.getItem(FOLDER_KEY) ?? "");
  const [asking, setAsking] = useState<string | null>(null); // item whose save card is open
  const [done, setDone] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    getReview()
      .then((r) => setItems(r.items))
      .catch(() => setItems(null));
  }

  useEffect(refresh, []);

  async function chooseDefault() {
    try {
      const dir = await pickFolder(folder);
      if (!dir) return;
      localStorage.setItem(FOLDER_KEY, dir);
      setFolder(dir);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveAs(item: ReviewItem) {
    try {
      const dir = await pickFolder(folder);
      if (dir) await resolve(item, "file", dir);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function resolve(item: ReviewItem, action: ReviewAction, dir?: string) {
    setAsking(null);
    setError(null);
    setBusy(true);
    try {
      const r = await resolveReview(item.id, action, action === "file" ? (names[item.id] ?? item.filename) : undefined, action === "file" ? (dir ?? (folder || undefined)) : undefined);
      setDone((d) => [`${item.title}: ${r.message}`, ...d]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
    refresh();
  }

  if (!items) return <p className="panel__empty">Could not load the review queue.</p>;

  return (
    <>
      <p className="panel__empty">
        Instructions the AI answered that will be lost when they leave the 24-hour log. Choose where each one lives, or
        let it go.
      </p>
      <div className="panel__actions">
        <span className="panel__label">Default save folder:</span>
        <span className="panel__mono">{folder || BUILT_IN_FOLDER}</span>
        <button type="button" className="panel__button" onClick={chooseDefault}>
          Choose folder…
        </button>
      </div>
      {error && <p className="panel__warning">{error}</p>}
      {items.length === 0 && <p className="panel__empty">Nothing at risk.</p>}
      <ul className="panel__list">
        {items.map((item) => (
          <li className="panel__list-item cls-row cls-row--keep" key={item.id}>
            <div className="panel__stack">
              <strong>
                {item.title} <ClassTag cls="keep" />
              </strong>
              <span className="panel__label">{item.preview}</span>
              <span className="panel__mono panel__label">
                {leavesIn(item.expires_at)}
                {item.versions > 1 && ` · ${item.versions} versions, newest shown`}
              </span>
              <div className="panel__actions">
                <input
                  className="panel__input panel__input--auto"
                  aria-label="File name"
                  value={names[item.id] ?? item.filename}
                  onChange={(e) => setNames({ ...names, [item.id]: e.target.value })}
                />
                <button type="button" className="panel__button" disabled={busy} onClick={() => setAsking(asking === item.id ? null : item.id)}>
                  Save to file
                </button>
                <button type="button" className="panel__button" disabled={busy} onClick={() => resolve(item, "memory")}>
                  Save to memory
                </button>
                <button
                  type="button"
                  className="panel__button panel__button--danger"
                  disabled={busy}
                  onClick={() => resolve(item, "discard")}
                >
                  Discard
                </button>
              </div>
              {asking === item.id && (
                <div className="panel__stack" role="group" aria-label="Where to save">
                  <span className="panel__label">Save to the default folder, or pick another?</span>
                  <div className="panel__actions">
                    <button type="button" className="panel__button panel__button--primary" disabled={busy} onClick={() => resolve(item, "file")}>
                      Use default ({folder || BUILT_IN_FOLDER})
                    </button>
                    <button type="button" className="panel__button" disabled={busy} onClick={() => saveAs(item)}>
                      Save as…
                    </button>
                    <button type="button" className="panel__button" onClick={() => setAsking(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
      {done.length > 0 && (
        <ul className="panel__list">
          {done.map((d) => (
            <li className="panel__empty" key={d}>
              {d}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default ReviewQueue;
