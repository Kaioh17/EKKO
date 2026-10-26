import { useEffect, useState } from "react";
import { deleteArchiveEntry, getArchive, type Archive } from "../../api/client";

function ArchiveMemory() {
  const [archive, setArchive] = useState<Archive | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    getArchive()
      .then(setArchive)
      .catch(() => setArchive(null))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  async function remove(key: string) {
    setError(null);
    try {
      await deleteArchiveEntry(key);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    refresh();
  }

  if (loading) return <p className="panel__empty">Loading…</p>;
  if (!archive) return <p className="panel__empty">Could not load the archive.</p>;
  if (!archive.entries.length)
    return (
      <p className="panel__empty">
        No archived days yet. A day appears here once its conversations are over 24 hours old.
      </p>
    );

  return (
    <>
      <p className="panel__empty">
        The AI sees only the titles (the newest {archive.key_cap}; {archive.keys_sent} sent now). Open an entry to see
        what it gets when it asks for that day.
      </p>
      {error && <p className="panel__warning">{error}</p>}
      <ul className="panel__list">
        {archive.entries.map((e) => (
          <li className="panel__list-item" key={e.key}>
            <details className="panel__stack">
              <summary>{e.key}</summary>
              <p>{e.summary}</p>
              {e.details && <p className="panel__label">{e.details}</p>}
              <span className="panel__mono panel__label">
                {e.turns} exchanges{e.topics.length ? ` · ${e.topics.join(", ")}` : ""}
              </span>
            </details>
            <button type="button" className="panel__button panel__button--danger" onClick={() => remove(e.key)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

export default ArchiveMemory;
