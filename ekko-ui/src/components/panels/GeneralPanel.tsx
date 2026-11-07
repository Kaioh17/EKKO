// Shared pieces of the merged "General & Voice" tab (see AudioPanel.tsx).
import { useEffect, useState } from "react";
import { getAutostart, setAutostart, type Autostart, type GeneralSettings } from "../../api/client";
import type { PageForm } from "./SettingsKit";


// "Start on sign-in" lives outside the settings DB (an OS autostart entry),
// so wrap it in the same draft/save shape as a settings form.
export function useAutostartForm(): PageForm & { draft: boolean | null; set: (v: boolean) => void; supported: boolean } {
  const [saved, setSaved] = useState<Autostart | null>(null);
  const [draft, setDraft] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAutostart()
      .then((a) => (setSaved(a), setDraft(a.enabled)))
      .catch(() => setSaved(null));
  }, []);

  const dirty = saved !== null && draft !== null && draft !== saved.enabled;
  return {
    draft,
    set: setDraft,
    supported: !!saved?.supported,
    dirty,
    busy,
    error,
    discard: () => setDraft(saved?.enabled ?? null),
    save: async () => {
      if (!dirty || draft === null) return true;
      setBusy(true);
      try {
        const a = await setAutostart(draft);
        setSaved(a);
        setDraft(a.enabled);
        setError(null);
        return true;
      } catch (e) {
        setError((e as Error).message);
        return false;
      } finally {
        setBusy(false);
      }
    },
    reset: async () => {
      setDraft(true); // recommended: on
      return true;
    },
  };
}

// Each switch is phrased positively; the stored flag is the negated "no_*" override.
export const CAN_DO: { key: keyof GeneralSettings; title: string; hint: string }[] = [
  { key: "no_execute", title: "Run commands on this laptop", hint: "Open apps, change settings, control media." },
  { key: "no_llm_fallback", title: "Ask a cloud AI when needed", hint: "If ekko canâ€™t answer locally, it asks an online model. Off = fully offline, but less capable." },
  { key: "no_manual_wake", title: "Wake with Ctrl+Alt+W", hint: "Start talking without the wake word." },
  { key: "no_hard_stop", title: "Stop everything with Ctrl+Alt+Q", hint: "Instantly cuts off speech and running tasks." },
];

