import "./ChatPanel.css";
import "./Dashboard.css";
import { useState, type KeyboardEvent } from "react";
import { sendChat, sendWake } from "../../api/client";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  model?: string | null;
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

  async function send(override?: string) {
    const text = (override ?? draft).trim();
    if (!text || sending) return;
    setMessages((prev) => [...prev, { id: prev.length, role: "user", text }]);
    setDraft("");
    setSending(true);
    try {
      const { reply, model } = await sendChat(text);
      setMessages((prev) => [...prev, { id: prev.length, role: "assistant", text: reply, model }]);
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

  return (
    <div className="chat-panel">
      <div className="dash__head">
        <p>Type to ekko when you can’t talk out loud.</p>
      </div>
      <div className="chat-panel__body">
        {started && (
          <div className="chat-panel__messages">
            {messages.map((message) => (
              <div key={message.id} className={`chat-bubble chat-bubble--${message.role}`}>
                {message.text}
                {message.model && <span className="chat-bubble__model">{message.model}</span>}
              </div>
            ))}
            {sending && <div className="chat-bubble chat-bubble--assistant chat-bubble--pending">…</div>}
          </div>
        )}
        <div className="chat-panel__box">
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
            <select className="chat-panel__cell chat-panel__select is-off" tabIndex={-1} title="Not available yet">
              <option>Auto (chain)</option>
            </select>
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
