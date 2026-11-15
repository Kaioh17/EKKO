import "./Panels.css";
import "./Settings.css";
import { useState } from "react";
import { CHAT_COLORS, getChatColor, setChatColor, type ChatColor } from "../../chatColor";
import { Row, SettingsSection } from "./SettingsKit";

function ChatSettings() {
  const [color, setColor] = useState<ChatColor>(getChatColor);

  function pick(next: ChatColor) {
    setColor(next);
    setChatColor(next);
  }

  return (
    <div className="panel panel--wide sset">
      <SettingsSection title="Appearance" hint="How your chat looks. Changes apply right away.">
        <Row title="Your message color" hint="The background of the messages you send.">
          {(labelId) => (
            <div className="chat-swatches" role="radiogroup" aria-labelledby={labelId}>
              {CHAT_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={c === color}
                  aria-label={c}
                  title={c}
                  className={`chat-swatch${c === color ? " is-on" : ""}`}
                  style={{ background: c === "gray" ? "var(--speak-bg)" : `var(--${COLOR_TOKEN[c]}-bg)` }}
                  onClick={() => pick(c)}
                />
              ))}
            </div>
          )}
        </Row>
        <div className="chat-bubble chat-bubble--user chat-swatch-preview">What's on my calendar today?</div>
      </SettingsSection>
    </div>
  );
}

// theme.css names the palettes after the avatar's states
const COLOR_TOKEN: Record<Exclude<ChatColor, "gray">, string> = {
  blue: "listen",
  yellow: "think",
  orange: "sorry",
  red: "red",
  green: "green",
};

export default ChatSettings;
