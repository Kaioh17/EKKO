import "./ChatPanel.css";
import "./Dashboard.css";
import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ProviderLogo from "../ProviderLogo";
import { acceptResearch, getProviders, streamChat, sendWake, type ChatStats, type ProviderInfo, type ResearchOffer } from "../../api/client";
import { CopyButton, ReplyMeta } from "./ReplyMeta";
import ContextView from "./ContextView";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  model?: string | null;
  stats?: ChatStats | null;
  offer?: ResearchOffer | null; // cleared once answered
}

const SEED_MESSAGES: ChatMessage[] = [
  { id: 0, role: "assistant", text: "Hey, I'm ekko. What do you need?" },
];

// Backend chat takes only a message: "+", "Do on my computer" and the model picker are shown grayed out.
const SUGGESTIONS = ["Summarise my unread email", "Why is my laptop slow?", "How much did I spend this week?"];

// UI-only history, separate from ekko's memory (day_log): clearing or losing this never touches memory.
const HISTORY_KEY = "ekko.chatHistory";
const HISTORY_MAX = 200; // ponytail: plain cap, move to a backend store if history needs search or sync

function loadHistory(): ChatMessage[] {
  try {
    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "null");
    return Array.isArray(saved) && saved.length ? saved : SEED_MESSAGES;
  } catch {
    return SEED_MESSAGES;
  }
}

function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>(loadHistory);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [live, setLive] = useState(""); // the reply as it streams in, before it lands in `messages`
  const boxRef = useRef<HTMLDivElement>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [provider, setProvider] = useState(""); // "" = auto (configured provider + failover)
  const [showContext, setShowContext] = useState(false);

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(messages.slice(-HISTORY_MAX)));
  }, [messages]);

  useEffect(() => {
    getProviders().then(setProviders).catch(() => {});
  }, []);

  async function send(override?: string) {
    const text = (override ?? draft).trim();
    if (!text || sending) return;
    if (text.toLowerCase() === "/context") {
      setShowContext(true);
      setDraft("");
      return;
    }
    setMessages((prev) => [...prev, { id: prev.length, role: "user", text }]);
    setDraft("");
    setSending(true);
    try {
      const { reply, model, stats, research_offer } = await streamChat(text, provider, setLive);
      setMessages((prev) => [...prev, { id: prev.length, role: "assistant", text: reply, model, stats, offer: research_offer }]);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Couldn't reach ekko's backend.";
      setMessages((prev) => [...prev, { id: prev.length, role: "assistant", text }]);
    } finally {
      setLive("");
      setSending(false);
    }
  }

  function answerOffer(index: number, yes: boolean) {
    const offer = messages[index].offer;
    setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, offer: null } : m)));
    if (yes && offer) acceptResearch(offer).catch(() => {});
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  const started = messages.length > 1;

  useEffect(() => {
    if (started) boxRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages, sending, started, live]);

  return (
    <div className={`chat-panel${started ? " chat-panel--started" : ""}`}>
      <div className="chat-panel__body">
        {started && (
          <div className="chat-panel__messages">
            {messages.map((message, i) => (
              <div key={i}className={`chat-bubble chat-bubble--${message.role}`}>
                {message.role === "assistant" ? (
                  <>
                    <div className="chat-md">
                      <Markdown remarkPlugins={[remarkGfm]}>{message.text}</Markdown>
                    </div>
                    <ReplyMeta text={message.text} model={message.model} stats={message.stats} />
                    {message.offer && (
                      <div className="chat-offer">
                        <span>Would you like to do further research on the web on Brave?</span>
                        <button type="button" onClick={() => answerOffer(i, true)}>Yes</button>
                        <button type="button" onClick={() => answerOffer(i, false)}>No</button>
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    {message.text}
                    <div className="chat-meta chat-meta--user">
                      <CopyButton text={message.text} />
                    </div>
                  </>
                )}
              </div>
            ))}
            {sending && (
              <div className="chat-bubble chat-bubble--assistant">
                {live ? (
                  <div className="chat-md">
                    <Markdown remarkPlugins={[remarkGfm]}>{live}</Markdown>
                  </div>
                ) : (
                  <div className="chat-thinking" role="status" aria-label="ekko is thinking">
                    <span /><span /><span />
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        {showContext && <ContextView onClose={() => setShowContext(false)} />}
        <div className="chat-panel__box" ref={boxRef}>
          <textarea
            className="chat-panel__input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message ekko, or type @ for an app…"
            rows={3}
          />
          <div className="chat-panel__bar">
            <button type="button" className="chat-panel__cell is-off" tabIndex={-1} title="Not available yet">+</button>
            <button type="button" className="chat-panel__cell chat-panel__cell--on">Ask</button>
            <button
              type="button"
              className={`chat-panel__cell${showContext ? " chat-panel__cell--on" : ""}`}
              title="See the memory ekko sends with your next message (or type /context)"
              onClick={() => setShowContext((o) => !o)}
            >
              Context
            </button>
            <button type="button" className="chat-panel__cell is-off" tabIndex={-1} title="Not available yet">Do on my computer</button>
            <div style={{ flex: 1 }} />
            <div
              className="chat-panel__picker"
              onBlur={(event) => !event.currentTarget.contains(event.relatedTarget) && setPickerOpen(false)}
            >
              <button
                type="button"
                className="chat-panel__cell chat-panel__select"
                title="Model for this chat"
                onClick={() => setPickerOpen((o) => !o)}
              >
                {provider && <ProviderLogo name={provider} />}
                {providers.find((p) => p.name === provider)?.label ?? "Auto (chain)"}
                <ChevronDown size={14} />
              </button>
              {pickerOpen && (
                <ul className="chat-panel__menu">
                  {[{ name: "", label: "Auto (chain)" }, ...providers.filter((p) => p.model)].map((p) => (
                    <li key={p.name}>
                      <button
                        type="button"
                        className={p.name === provider ? "is-on" : ""}
                        onClick={() => { setProvider(p.name); setPickerOpen(false); }}
                      >
                        {p.name && <ProviderLogo name={p.name} />}
                        {p.label}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button
              type="button"
              className="chat-panel__talk"
              title={draft.trim() ? "Send" : "Talk"}
              onClick={() => (draft.trim() ? send() : void sendWake().catch(() => {}))}
              disabled={sending}
            >
              {draft.trim() ? "Send" : "Talk"}
            </button>
          </div>
        </div>
        {!started && (
          <div className="chat-panel__suggest">
            {SUGGESTIONS.map((q) => (
              <button type="button" key={q} onClick={() => send(q)}>
                <span>{q}</span>
                <span>↗</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default ChatPanel;
