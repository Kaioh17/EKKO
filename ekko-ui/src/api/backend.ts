import { invoke, isTauri } from "@tauri-apps/api/core";

export interface BackendInfo {
  url: string;
  token: string;
}

let info: Promise<BackendInfo> | null = null;

// The Rust side starts (or attaches to) the backend and hands over its
// per-launch URL and token; nothing secret is compiled into this bundle.
// Plain `pnpm dev` in a browser has no Tauri, so it reads VITE_* vars,
// dev builds only.
export function backend(): Promise<BackendInfo> {
  info ??= isTauri()
    ? invoke<BackendInfo>("backend_info")
    : import.meta.env.DEV
      ? Promise.resolve({
          url: import.meta.env.VITE_EKKO_BACKEND_URL ?? "http://127.0.0.1:8765",
          token: import.meta.env.VITE_EKKO_API_TOKEN ?? "",
        })
      : Promise.reject(new Error("ekko must run inside the desktop app"));
  info.catch(() => (info = null)); // let a later call retry
  return info;
}
