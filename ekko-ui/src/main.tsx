import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter-tight/500.css";
import "@fontsource/inter-tight/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/space-grotesk/500.css";
import "./styles/theme.css";
import { getCurrentWindow } from "@tauri-apps/api/window";
import App from "./App";
import Avatar from "./components/Avatar";
import Sandbox from "./components/Sandbox";

// One bundle, three windows: the overlays are told apart by their tauri.conf label.
const View = { avatar: Avatar, sandbox: Sandbox }[getCurrentWindow().label as "avatar" | "sandbox"] ?? App;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <View />
  </React.StrictMode>,
);
