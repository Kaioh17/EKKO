import { useEffect, useState } from "react";
import { getModels, type ModelsStatus } from "../api/client";

const POLL_MS = 3000;

// First run: ekko downloads its voice models (~65 MB) before the listener
// starts. Polls until that's done, then disappears.
function SetupBanner() {
  const [status, setStatus] = useState<ModelsStatus | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;
    const poll = () =>
      getModels()
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          if (s.phase !== "ready" && s.missing.length > 0) timer = setTimeout(poll, POLL_MS);
        })
        .catch(() => !cancelled && (timer = setTimeout(poll, POLL_MS)));
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  if (!status || status.missing.length === 0) return null;
  return (
    <div className="update-banner" role="status">
      {status.phase === "failed" ? (
        <span className="panel__warning">Couldn't download the voice models: {status.error}. Check your connection and restart ekko.</span>
      ) : (
        <span>Setting up ekko: downloading {status.current ?? "voice models"}…</span>
      )}
    </div>
  );
}

export default SetupBanner;
