import "./styles/theme.css";
import "./App.css";
import { useState } from "react";
import TopBar from "./components/TopBar";
import CentralStage from "./components/CentralStage";
import Sidebar, { type Section } from "./components/Sidebar";
import MemoryPanel from "./components/panels/MemoryPanel";
import RoutingPanel from "./components/panels/RoutingPanel";
import HotkeysPanel from "./components/panels/HotkeysPanel";
import ChatPanel from "./components/panels/ChatPanel";
import BriefingPanel from "./components/panels/BriefingPanel";
import ModelsPanel from "./components/panels/ModelsPanel";
import GeneralPanel from "./components/panels/GeneralPanel";
import HelpPanel, { type HelpTopicId } from "./components/panels/HelpPanel";
import PlaceholderPanel from "./components/panels/PlaceholderPanel";
import { useStatusSocket } from "./hooks/useStatusSocket";
import { sendWake } from "./api/client";

function App() {
  const [section, setSection] = useState<Section>("General");
  const [helpTopic, setHelpTopic] = useState<HelpTopicId | undefined>();
  const status = useStatusSocket();

  function select(next: Section) {
    setHelpTopic(undefined);
    setSection(next);
  }

  function openHelp(topic: HelpTopicId) {
    setHelpTopic(topic);
    setSection("Help");
  }

  function renderPanel() {
    switch (section) {
      case "General":
        return <GeneralPanel status={status} onHelp={openHelp} />;
      case "Chat":
        return <ChatPanel />;
      case "Briefing":
        return <BriefingPanel />;
      case "Models":
        return <ModelsPanel />;
      case "Memory":
        return <MemoryPanel />;
      case "Routing":
        return <RoutingPanel />;
      case "Hotkeys":
        return <HotkeysPanel />;
      case "Help":
        return <HelpPanel topic={helpTopic} />;
      default:
        return <PlaceholderPanel section={section} />;
    }
  }

  return (
    <div className="app-shell">
      <Sidebar active={section} onSelect={select} />
      <div className="app-content">
        <TopBar status={status} onWake={sendWake} />
        <main className="app-main">
          <CentralStage>{renderPanel()}</CentralStage>
        </main>
      </div>
    </div>
  );
}

export default App;
