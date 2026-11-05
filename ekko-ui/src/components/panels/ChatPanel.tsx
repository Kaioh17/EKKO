import "./ChatPanel.css";
import "./Dashboard.css";
import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ProviderLogo from "../ProviderLogo";
import { getProviders, sendChat, sendWake, type ChatStats, type ProviderInfo } from "../../api/client";
import { CopyButton, ReplyMeta } from "./ReplyMeta";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  model?: string | null;
  stats?: ChatStats | null;
}

const SEED_MESSAGES: ChatMessage[] = [
  { id: 0, role: "assistant", text: "Hey, I'm ekko. What do you need?" },
];

// Backend chat takes only a message: "+", "Do on my computer" and the model picker are shown grayed out.
const SUGGESTIONS = ["Summarise my unread email", "Why is my laptop slow?", "How much did I spend this week?"];

function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>(SEED_MESSAGES);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [provider, setProvider] = useState(""); // "" = auto (configured provider + failover)

  useEffect(() => {
    getProviders().then(setProviders).catch(() => {});
  }, []);

  async function send(override?: string) {
    const text = (override ?? draft).trim();
    if (!text || sending) return;
    setMessages((prev) => [...prev, { id: prev.length, role: "user", text }]);
    setDraft("");
    setSending(true);
    try {
      const { reply, model, stats } = await sendChat(text, provider);
      setMessages((prev) => [...prev, { id: prev.length, role: "assistant", text: reply, model, stats }]);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Couldn't reach ekko's backend.";
      setMessages((prev) => [...prev, { id: prev.length, role: "assistant", text }]);
    } finally {
      setSending(false);
    }
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
  }, [messages, sending, started]);

  return (
    <div className={`chat-panel${started ? " chat-panel--started" : ""}`}>
      <div className="chat-panel__body">
        {started && (
          <div className="chat-panel__messages">
            {messages.map((message) => (
              <div key={message.id} className={`chat-bubble chat-bubble--${message.role}`}>
                {message.role === "assistant" ? (
                  <>
                    <div className="chat-md">
                      <Markdown remarkPlugins={[remarkGfm]}>{message.text}</Markdown>
                    </div>
                    <ReplyMeta text={message.text} model={message.model} stats={message.stats} />
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
            {sending && <div className="chat-bubble chat-bubble--assistant chat-bubble--pending">…</div>}
          </div>
        )}
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
