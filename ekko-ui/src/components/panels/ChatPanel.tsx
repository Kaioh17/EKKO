import "./ChatPanel.css";
import { useState, type KeyboardEvent } from "react";
import { sendChat } from "../../api/client";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  model?: string | null;
}

const SEED_MESSAGES: ChatMessage[] = [
  { id: 0, role: "assistant", text: "Hey, I'm Ekko. What do you need?" },
];

function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>(SEED_MESSAGES);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    setMessages((prev) => [...prev, { id: prev.length, role: "user", text }]);
    setDraft("");
    setSending(true);
    try {
      const { reply, model } = await sendChat(text);
      setMessages((prev) => [...prev, { id: prev.length, role: "assistant", text: reply, model }]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { id: prev.length, role: "assistant", text: "Couldn't reach Ekko's backend." },
      ]);
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

  return (
    <div className="chat-panel">
      <div className="chat-panel__messages">
        {messages.map((message) => (
          <div key={message.id} className={`chat-bubble chat-bubble--${message.role}`}>
            {message.text}
            {message.model && <span className="chat-bubble__model">{message.model}</span>}
          </div>
        ))}
        {sending && <div className="chat-bubble chat-bubble--assistant chat-bubble--pending">…</div>}
      </div>
      <div className="chat-panel__composer">
        <textarea
          className="chat-panel__input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message Ekko…"
          rows={1}
        />
        <button type="button" className="chat-panel__send" onClick={send} disabled={!draft.trim() || sending}>
          Send
        </button>
      </div>
    </div>
  );
}

export default ChatPanel;
