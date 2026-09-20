import { useEffect, useRef, useState } from "react";
import { getSettings, patchSettings, resetSettings } from "../api/client";

// Draft/save/reset for one backend settings section. Edits stay local
// until save(); reset() drops the section back to code defaults.
export function useSettingsForm<T extends object>(section: string) {
  const [saved, setSaved] = useState<T | null>(null);
  const [draft, setDraft] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // save() can fire right after an input's blur commit; read the latest draft, not a stale closure.
  const draftRef = useRef(draft);
  draftRef.current = draft;

  function apply(next: T) {
    setSaved(next);
    setDraft(next);
    setError(null);
  }

  useEffect(() => {
    getSettings<T>(section)
      .then(apply)
      .catch(() => setError("Settings unavailable."))
      .finally(() => setLoading(false));
  }, [section]);

  const changes = (): Partial<T> => {
    const d = draftRef.current;
    if (!d || !saved) return {};
    return Object.fromEntries(
      Object.entries(d).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(saved[k as keyof T])),
    ) as Partial<T>;
  };

  async function run(call: () => Promise<T>) {
    setBusy(true);
    try {
      apply(await call());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return {
    saved,
    draft,
    loading,
    busy,
    error,
    dirty: Object.keys(changes()).length > 0,
    set: (patch: Partial<T>) => setDraft((d) => d && { ...d, ...patch }),
    save: () => {
      const c = changes();
      if (Object.keys(c).length) return run(() => patchSettings<T>(section, c));
    },
    reset: () => run(() => resetSettings<T>(section)),
  };
}
