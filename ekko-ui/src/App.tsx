import "./styles/theme.css";
import "./App.css";
import { useRef, useState } from "react";
import { GuardContext, UnsavedDialog, type Guard } from "./components/panels/SettingsKit";
import TopBar from "./components/TopBar";
import UpdateBanner from "./components/UpdateBanner";
import SetupBanner from "./components/SetupBanner";
import CentralStage from "./components/CentralStage";
import Sidebar, { type Section } from "./components/Sidebar";
import SettingsOverlay from "./components/SettingsOverlay";
import MemoryPanel from "./components/panels/MemoryPanel";
import ConnectionsPanel from "./components/panels/ConnectionsPanel";
import RoutingPanel from "./components/panels/RoutingPanel";
import HotkeysPanel from "./components/panels/HotkeysPanel";
import ChatPanel from "./components/panels/ChatPanel";
import BriefingPanel from "./components/panels/BriefingPanel";
import ModelsPanel from "./components/panels/ModelsPanel";
import SearchPanel from "./components/panels/SearchPanel";
import HomePanel from "./components/panels/HomePanel";
import OverviewPanel from "./components/panels/OverviewPanel";
import AudioPanel from "./components/panels/AudioPanel";
import HelpPanel, { type HelpTopicId } from "./components/panels/HelpPanel";
import PlaceholderPanel from "./components/panels/PlaceholderPanel";
import RestartingScreen from "./components/RestartingScreen";
import { useStatusSocket } from "./hooks/useStatusSocket";
import { sendWake } from "./api/client";

function App() {
  const [section, setSection] = useState<Section>("Home");
  const [helpTopic, setHelpTopic] = useState<HelpTopicId | undefined>();
  const [query, setQuery] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tab, setTab] = useState<Section>("General");
  const status = useStatusSocket();
  const [advanced, setAdvanced] = useState(() => localStorage.getItem("usageAdvanced") === "1");
  function toggleAdvanced(v: boolean) {
    localStorage.setItem("usageAdvanced", v ? "1" : "0");
    setAdvanced(v);
  }

  // The open settings page registers itself here; leaving it with edits asks first.
  const guard = useRef<Guard | null>(null);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const leave = (go: () => void) => (guard.current?.dirty ? setPending(() => go) : go());

  function select(next: Section) {
    const go = () => {
      setHelpTopic(undefined);
      setQuery("");
      setSettingsOpen(false);
      setSection(next);
    };
    next === section && !settingsOpen ? go() : leave(go);
  }

  function openHelp(topic: HelpTopicId) {
    leave(() => {
      setSettingsOpen(false);
      setHelpTopic(topic);
      setSection("Help");
    });
  }

  const closeSettings = () => leave(() => setSettingsOpen(false));
  const selectTab = (next: Section) => next !== tab && leave(() => setTab(next));

  async function resolve(save: boolean) {
    const go = pending;
    setPending(null);
    if (save ? !(await guard.current?.save()) : (guard.current?.discard(), false)) return; // save failed: stay, error shows on the page
    go?.();
  }

  function renderPanel(section: Section) {
    switch (section) {
      case "Home":
        return <HomePanel onNavigate={select} />;
      case "Usage":
        return <OverviewPanel status={status} advanced={advanced} />;
      case "General":
        return <AudioPanel onHelp={openHelp} />;
      case "Chat":
        return <ChatPanel />;
      case "Briefing":
        return <BriefingPanel />;
      case "Models":
        return <ModelsPanel />;
      case "Search":
        return <SearchPanel />;
      case "Memory":
        return <MemoryPanel />;
      case "Connections":
        return <ConnectionsPanel />;
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
    <GuardContext.Provider value={guard}>
    <div className="app-shell">
      <RestartingScreen />
      {pending && <UnsavedDialog onSave={() => void resolve(true)} onDiscard={() => void resolve(false)} onStay={() => setPending(null)} />}
      {settingsOpen && (
        <SettingsOverlay tab={tab} onTab={selectTab} onClose={closeSettings}>
          {renderPanel(tab)}
        </SettingsOverlay>
      )}
      <Sidebar
        active={section}
        status={status}
        settingsOpen={settingsOpen}
        onSelect={select}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <div className="app-content">
        <SetupBanner />
        <UpdateBanner />
        <TopBar status={status} section={section} onWake={sendWake} query={query} onQuery={setQuery} advanced={advanced} onAdvanced={toggleAdvanced} />
        <main className="app-main">
          <CentralStage>{renderPanel(section)}</CentralStage>
        </main>
      </div>
    </div>
    </GuardContext.Provider>
  );
}

export default App;
