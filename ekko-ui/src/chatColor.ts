// Colour of your own chat bubble. A per-device look, so it lives in localStorage like the Usage toggle;
// theme.css turns data-chat-color on <html> into the --chat-* variables.
export const CHAT_COLORS = ["gray", "blue", "yellow", "orange", "red", "green"] as const;
export type ChatColor = (typeof CHAT_COLORS)[number];

const KEY = "ekko.chatColor";

export function getChatColor(): ChatColor {
  try {
    const saved = localStorage.getItem(KEY);
    return CHAT_COLORS.find((c) => c === saved) ?? "gray";
  } catch {
    return "gray";
  }
}

export function applyChatColor(color: ChatColor = getChatColor()) {
  document.documentElement.dataset.chatColor = color;
}

export function setChatColor(color: ChatColor) {
  try {
    localStorage.setItem(KEY, color);
  } catch {
    /* applies for this session only */
  }
  applyChatColor(color);
}
