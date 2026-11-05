import "./styles/theme.css";
import "./App.css";
import { useState } from "react";
import TopBar from "./components/TopBar";
import UpdateBanner from "./components/UpdateBanner";
import SetupBanner from "./components/SetupBanner";
import CentralStage from "./components/CentralStage";
import Sidebar, { type Section } from "./components/Sidebar";
import MemoryPanel from "./components/panels/MemoryPanel";
import RoutingPanel from "./components/panels/RoutingPanel";
import HotkeysPanel from "./components/panels/HotkeysPanel";
import ChatPanel from "./components/panels/ChatPanel";
import BriefingPanel from "./components/panels/BriefingPanel";
import ModelsPanel from "./components/panels/ModelsPanel";
import OverviewPanel from "./components/panels/OverviewPanel";
import AudioPanel from "./components/panels/AudioPanel";
import GeneralPanel from "./components/panels/GeneralPanel";
import HelpPanel, { type HelpTopicId } from "./components/panels/HelpPanel";
import PlaceholderPanel from "./components/panels/PlaceholderPanel";
import { useStatusSocket } from "./hooks/useStatusSocket";
import { sendWake } from "./api/client";

function App() {
  const [section, setSection] = useState<Section>("Overview");
  const [helpTopic, setHelpTopic] = useState<HelpTopicId | undefined>();
  const [query, setQuery] = useState("");
  const status = useStatusSocket();

  function select(next: Section) {
    setHelpTopic(undefined);
    setQuery("");
    setSection(next);
  }

  function openHelp(topic: HelpTopicId) {
    setHelpTopic(topic);
    setSection("Help");
  }

  function renderPanel() {
    switch (section) {
      case "Overview":
        return <OverviewPanel status={status} />;
      case "General":
        return <GeneralPanel status={status} />;
      case "Audio":
        return <AudioPanel onHelp={openHelp} query={query} />;
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
        return <PlaceholderPanel />;
    }
  }

  return (
    <div className="app-shell">
      <Sidebar active={section} status={status} onSelect={select} />
      <div className="app-content">
        <SetupBanner />
        <UpdateBanner />
        <TopBar status={status} section={section} onWake={sendWake} query={query} onQuery={setQuery} />
        <main className="app-main">
          <CentralStage>{renderPanel()}</CentralStage>
        </main>
      </div>
    </div>
  );
}

export default App;
