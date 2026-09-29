import { useEffect, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

// Checks GitHub Releases once per launch. The update is signature-checked
// by the updater plugin before it installs (tauri.conf.json plugins.updater).
function UpdateBanner() {
  const [update, setUpdate] = useState<Update | null>(null);
  const [state, setState] = useState<"idle" | "installing" | "failed">("idle");

  useEffect(() => {
    if (isTauri() && !import.meta.env.DEV) check().then(setUpdate).catch(() => {});
  }, []);

  if (!update) return null;
  return (
    <div className="update-banner" role="status">
      <span>ekko {update.version} is available.</span>
      <button
        type="button"
        className="panel__button panel__button--primary"
        disabled={state === "installing"}
        onClick={() => {
          setState("installing");
          update
            .downloadAndInstall()
            .then(relaunch)
            .catch(() => setState("failed"));
        }}
      >
        {state === "installing" ? "Updating…" : "Update and restart"}
      </button>
      {state === "failed" && <span className="panel__warning">Update failed, try again later.</span>}
    </div>
  );
}

export default UpdateBanner;
