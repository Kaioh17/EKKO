import { useEffect, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { subscribe } from "../api/socket";

const GIVE_UP_MS = 90_000; // a listener that never reports ready (crash loop) must not trap the UI

async function notify() {
  if (!isTauri()) return;
  const { isPermissionGranted, requestPermission, sendNotification } = await import("@tauri-apps/plugin-notification");
  if ((await isPermissionGranted()) || (await requestPermission()) === "granted") {
    sendNotification({ title: "ekko is ready", body: "Your settings were applied." });
  }
}

// Full-window loading page while the backend restarts the listener (a saved
// setting, the Restart button); a desktop notification when it's back up.
function RestartingScreen() {
  const [restarting, setRestarting] = useState(false);

  useEffect(
    () =>
      subscribe((data) => {
        const msg = data as { type?: string; state?: string };
        if (msg?.type !== "listener") return;
        setRestarting((was) => {
          if (msg.state === "ready" && was) void notify().catch(() => {}); // a first launch's ready isn't news
          return msg.state === "restarting";
        });
      }),
    [],
  );

  useEffect(() => {
    if (!restarting) return;
    const id = setTimeout(() => setRestarting(false), GIVE_UP_MS);
    return () => clearTimeout(id);
  }, [restarting]);

  if (!restarting) return null;
  return (
    <div className="restarting" role="alert" aria-live="assertive">
      <div className="restarting__spinner" aria-hidden="true" />
      <h2 className="restarting__title">Restarting ekko</h2>
      <p className="restarting__body">Applying your settings. This takes a few seconds.</p>
    </div>
  );
}

export default RestartingScreen;
